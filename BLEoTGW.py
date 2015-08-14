#!/usr/bin/env python
# -*- coding: utf-8 -*-
import os, sys, argparse, serial, time, datetime, json, signal, logging
import requests
import bglib
import util
#import bglib, serial, time, datetime,json, array, signal, requests, os, sys, zlib, argparse

#----------------------------------------
# BLE state machine definitions
#----------------------------------------
BLE_STATE_STANDBY = 0
BLE_STATE_SCANNING = 1
BLE_STATE_ADVERTISING = 2
BLE_STATE_CONNECTING = 3
BLE_STATE_CONNECTED_MASTER = 4
BLE_STATE_CONNECTED_SLAVE = 5
#----------------------------------------
DEBUG = True



class BleCon(object):
    def __init__(self, url):
        self.address = None #Bluetooth ID of the client
        self.tx = Tx(url)
        self.rx = Rx(url)

class BLEoTG(object):
    def __init__(self, port_name='', adv_name='', url=''):
        self.bglib = bglib.BGLib()
        self.port_name = port_name
        self.adv_name = adv_name
        self.url = url
        self.baud_rate = 38400
        self.packet_mode = False
        self.major = 0x0001
        self.minor = 0x0001
        self.adv_min = 48
        self.adv_max = 64
        self.serial = None
        self.state = None
        self.tx_handle = 21
        self.rx_handle = 24 #FIXME, check if Nos are correct
        # NOTE
        # 0=gap_non_discoverable, 1=gap_limited_discoverable,
        # 2=gap_general_discoverable, 3=gap_broadcast, 4=gap_user_data
        self.discoverable = 4
        # 0=gap_non_connectable, 1=gap_directed_connectable,
        # 2=gap_undirected_connectable, 3=gap_scannable_non_connectable
        self.connectable = 2
        self.known_clients = {}
        self.blacklisted_clients = {}
        self.active_client = None
        #dddd344432310000290702000000030000
        self.adv_data = [
                0x08, # field length
                0x08, # BGLIB_GAP_AD_TYPE_LOCALNAME_COMPLETE --> field type 0x08=shortname
                0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, # adv name
                #0x99, 0x54, 0x55, 0x41, 0x43, 0x54, 0x55, 0x41, 0x54, 0x31, # adv name
                0x02, # field length
                0x01, # BGLIB_GAP_AD_TYPE_FLAGS --> field type
                0x06, # data (0x02 | 0x04 = 0x06, general discoverable + BLE only, no BR+EDR)
                0x12, # field length
                0xFF, # BGLIB_GAP_AD_TYPE_SERVICES_128BIT_ALL --> field type
                #0xDD, 0xDD, 0x3B, 0x34, 0x44, 0x32, 0x31, 0x04, 0x00, 0x02 # DDDD_HEADER_4D21_TYPE_COORDINATES_MISC
                0xDD, 0xDD, 0x34, 0x44, 0x32, 0x31, 0x00, 0x00, 0x01, 0x0A, 0x00, 0x00, 0x00, 0x00, 0x01, 0x99, 0x99 # DDDD_HEADER_4D21_TYPE_COORDINATES_MISC
                ]
        self.adv_data[2:9] = util.get_char_array(self.adv_name)[0:7]
        logging.debug('Advertising Data: %s', self.adv_data)
        
        # build custom scan response data
        # default BLE stack value: 140942474c69622055314131502033382e344e4657
        # NOTE there is no need for scan response data, this is the second
        # packet that we could send when a master scans
        # self.sr_data = [
        #         0x06, # field length
        #         0x09, # BGLIB_GAP_AD_TYPE_LOCALNAME_COMPLETE --> field type 0x08=shortname
        #         0x42, 0x6C, 0x65, 0x50, 0x69
        #         ]

    def setup(self):
        logging.info('Setting up BLEoTGateway...')
        self.bglib.packet_mode = self.packet_mode
        self.ser = serial.Serial(port=self.port_name, baudrate=self.baud_rate, timeout=1)
        logging.debug('flushing input')
        self.ser.flushInput()
        logging.debug('flushing output')
        self.ser.flushOutput()

        # disconnect if we are connected already
        logging.debug('disconnecting in case we are connected')
        self.bglib.send_command(self.ser, self.bglib.ble_cmd_connection_disconnect(0))
        self.bglib.check_activity(self.ser, 1)

        # stop advertising if we are advertising already
        logging.debug('stop advertising in case we are still')
        # 0 gap_non_discoverable, 0 gap_non_connectable
        self.bglib.send_command(self.ser, self.bglib.ble_cmd_gap_set_mode(0, 0))
        self.bglib.check_activity(self.ser, 1)
        
        # stop scanning if we are scanning already
        # This command ends the current GAP discovery procedure and stop the scanning
        # of advertising devices
        logging.debug('stop scanning in case we are still')
        self.bglib.send_command(self.ser, self.bglib.ble_cmd_gap_end_procedure())
        self.bglib.check_activity(self.ser, 1)

        # now we musst be in STANDBY state
        self.state = BLE_STATE_STANDBY
        logging.debug('BLEoTGateway is in STANDBY now.')

        logging.debug('set advertisement (min/max interval + all three ad channels)')
        # This is just to tweak channels, radio...
        self.bglib.send_command(self.ser, self.bglib.ble_cmd_gap_set_adv_parameters(
            int(self.adv_min * 0.625), int(self.adv_max * 0.625), 7))
        self.bglib.check_activity(self.ser, 1)

        # 4 means custom user data in advertisment packet
        if self.discoverable == 4:
            logging.debug('Setting user defined advertising data.')
            self.bglib.send_command(self.ser,
                    self.bglib.ble_cmd_gap_set_adv_data(0, self.adv_data))
            self.bglib.check_activity(self.ser, 1)

            # NOTE SR is not necessary
            # print 'set local name (scan response packet)'
            # self.bglib.send_command(self.ser, self.bglib.ble_cmd_gap_set_adv_data(1,
            #     self.sr_data))
            # self.bglib.check_activity(self.ser, 1)
        # start advertising as discoverable with user data (4) and connectable (2)
        # ibeacon was 0x84, 0x03
        logging.debug('Entering advertising mode.')
        self.bglib.send_command(self.ser, self.bglib.ble_cmd_gap_set_mode(
            self.discoverable, self.connectable))
        self.bglib.check_activity(self.ser, 1)

        # set state to advertising
        self.state = BLE_STATE_ADVERTISING 
        logging.debug('BLEoTGateway is in BLE_STATE_ADVERTISING now.')

        # FIXME is a timeout really serious? can we do anything here,
        # except to notice that we had a timeout?
        # handler to notify of an API parser timeout condition

    def update(self):
        logging.info("Updating BLEBridge")

    def updateFromRest(self):
        '''
        Updates the available devices and their states from the REST interface.
        The new state is perisisted in new advertisement messages.
        '''
        logging.info("Reading content of RESTful interface")


#------------------------------------------------------------------------------
# Event Handlers
#------------------------------------------------------------------------------
    def handler_on_timeout(self, sender, args):
        '''
        Gets called when we send a command but we don't get a response back e.g.
        '''
        logging.debug('handler_on_timeout: %s', args)
        # might want to try the following lines to reset, though it probably
        # wouldn't work at this point if it's already timed out:
        self.bglib.send_command(self.ser, self.bglib.ble_cmd_system_reset(0))
        self.state = BLE_STATE_STANDBY
        logging.debug('BLEoTGateway is in STANDBY now.')

    # NOTE not needed as we are not in enhanced broadcasting mode,
    # This will never get called
    def handler_ble_evt_gap_scan_response(self, sender, args):
        """
        Handler to print scan responses with a timestamp (this only works when
        discoverable mode is set to enhanced broadcasting 0x80/0x84)
        """

    def handler_ble_evt_connection_status(self, sender, args):
        '''
        This gets called when a client has just been connected to us.
        We need to check if we already know it, potentially check if it is blacklisted etc...
        Then we set it as our active client.
        '''
        logging.debug('handler_ble_evt_connection_status: %s', args)
        logging.info('New client connection.')

        # check if connection status flags are 5 (bit0=connection_connected + bit2=connection completed)
        if (args['flags'] & 0x05) == 0x05:
            self.state = BLE_STATE_CONNECTED_SLAVE
            logging.debug('BLEoTGateway is in BLE_STATE_CONNECTED_SLAVE now.')
            # now let's keep the clients address as its unique ID and set it as active
            client_add = tuple(args['address'])
            connection = args['connection']
            if client_add in self.blacklisted_clients:
                self.bgli.send_command(self.ser, self.bglib.ble_cmd_connection_disconnect(connection))
            else:
                if client_add not in self.known_clients:
                    con = BleCon(self.url) #Create a new connection object for the new client
                    self.known_clients[client_add] = con
                else:
                    con = self.known_clients[client_add]
                self.active_client = con
                if self.active_client.tx.packetno == 0:
                    self.active_client.tx.update_json() #lets keep our state regularly updated
            logging.debug('Requesting to upgrade connection')
            self.bglib.send_command(self.ser, self.bglib.ble_cmd_connection_update(
                connection, 6, 24, 0, 25))
        else:
            logging.warning('Connection was not correctly established!')

    def handler_ble_evt_connection_disconnected(self, sender, args):
        '''
        A client has just disconnected from us
        '''
        logging.debug('handler_ble_evt_connection_disconnected: %s', args)
        if args['reason'] == 0x213:
            logging.debug('User on the remote device terminated the connection')

        # Remove the active client from our app (even this should not be necessary)
        self.active_client = None
        # We need to advertise ourselves again as a slave
        self.bglib.send_command(self.ser, self.bglib.ble_cmd_gap_set_mode(
            self.discoverable, self.connectable))

        # we can now set state to advertise again
        self.state = BLE_STATE_ADVERTISING
        logging.debug('BLEoTGateway is in BLE_STATE_ADVERTISING now.')
        

    def handler_ble_rsp_gap_set_mode(self, senser, args):
        '''
        GAP mode has been set in response to
        self.bglib.ble_cmd_gap_set_mode
        '''
        logging.debug('handler_ble_rsp_gap_set_mode: %s', args)
        if args["result"] != 0:
            logging.warning('ble_rsp_gap_set_mode FAILED\n Re-running setup()')
            self.setup()
        else:
            logging.debug('GAP mode sucessfully set')

    def handler_ble_evt_attributes_value(self, sender, args):
        '''
        Gets called when an attribute has been written by a client
        {'connection': 0, 'handle': 25, 'reason': 2, 'value': [1], 'offset': 0}
        '''
        logging.debug('handler_ble_evt_attributes_value: %s', args)
        # RX characteristic
        if args['handle'] == self.rx_handle:
            value = args['value']
            logging.debug('client is writting a command')
            if self.active_client.rx.packetno == 0:
                # cleanout old data TODO might be good to keep track of past commands?
                self.active_client.rx.data = []
            for char in value:
                print "char "+str(unichr(char))
                self.active_client.rx.data.append(char)
            logging.debug('Data received: %s',self.active_client.rx.data)
            self.active_client.rx.packetno += 1
            self.bglib.send_command(self.ser, self.bglib.ble_cmd_attributes_user_write_response(args['connection'], 0x0))
            #if len(value) < 22:
            if value[-1] == 0x0A: # check for new line character at the end
                logging.debug('last packet received')
                # resetting packet no
                self.active_client.rx.packetno = 0
                # NOTE Now, we need to parse what we have received and then execute it
                for char in self.active_client.rx.data[0:-1]: #process all but last character
                    self.active_client.rx.request = self.active_client.rx.request + str(unichr(char))
                logging.debug('full request: %s', self.active_client.rx.request)
                self.active_client.rx.success = self.active_client.rx.send_request()
                self.active_client.rx_message = ''
                self.active_client.rx.data = []
                self.active_client.rx.request = ''

    def handler_ble_evt_attributes_user_read_request(self, sender, args):
        '''
        This is called whenever a client reads an attribute that has the user type 
        enabled. We then serve the data dynamically. Each packet payload is 22 Bytes.
        Whenever a client is receiving 22 bytes, the client needs to issue another
        read request on the same atttribute as long as all the data is received
        (This can be concludes when we receive less than 22 bytes)
        'connection': connection, 'handle': handle, 'offset': offset, 'maxsize': maxsize
        '''
        logging.debug('handler_ble_evt_attributes_user_read_request: %s', args)
        client_con = args['connection'] # --> we should not care about connection No for identification
        # as we only can have one single client connected at the same time
        # for feedback if previous write has been suceeded
        if self.active_client !=None: # this can happen if there for some reason old data in buffer
            if args['handle'] == self.rx_handle: # Handle 24 is TX
                logging.debug('Read request on TX handle')
                if self.active_client.rx.success:
                    value = [0x01]
                else:
                    value = [0x00]
                self.bglib.send_command(self.ser, self.bglib.ble_cmd_attributes_user_read_response(client_con, 0, value))
            if args['handle'] == self.tx_handle: # Handle 21 is TX
                logging.debug('Read request on RX handle')
                logging.debug('Packet No: %s', self.active_client.tx.packetno)
                value = util.get_byte_packet(self.active_client.tx.packetno, self.active_client.tx.frameno, self.active_client.tx.data)
                if len(value) < 22:
                    self.active_client.tx.packetno = 0
                    if self.active_client.tx.frameno == (len(self.active_client.tx.data)-1):
                        logging.debug('Transmitted all packets')
                        self.active_client.tx.frameno = 0 # we have actually transimitted all data
                    else:
                        self.active_client.tx.frameno+=1

                else:
                    self.active_client.tx.packetno+=1
                self.bglib.send_command(self.ser, self.bglib.ble_cmd_attributes_user_read_response(client_con, 0, value))
        # this should not happen, only if we have some old data, let's reset
        else:
            logging.warning('Unexpectedly no client found, resetting BLEoTGateway')
            self.bglib.send_command(self.ser, self.bglib.ble_cmd_system_reset(0))

    def handler_ble_rsp_connection_update(self, sender, args):
        '''
            Gets called as a result of us trying to upgrade an existing connection.
        '''
        logging.debug('handler_ble_rsp_connection_update: %s', args)
        if args['result'] != 0:
            logging.warning('Upgrading od connection failed, resetting BLEoTGateway')
            self.bglib.send_command(self.ser, self.bglib.ble_cmd_system_reset(0))
            self.state = BLE_STATE_STANDBY
            logging.debug('BLEoTGateway is in STANDBY now.')

    def handler_ble_evt_attributes_status(self, sender, args):
        '''
            Gets called when a client enables notification or indication
        '''
        logging.debug('handler_ble_evt_attributes_status: %s', args)

    def handler_ble_rsp_attributes_read(self, sender, args):
        logging.debug('handler_ble_rsp_attributes_read: %s', args)

    def handler_ble_rsp_attributes_user_read_response(self, sender, args):
        logging.debug('handler_ble_rsp_attributes_user_read_response: %s', args)

    def handler_ble_rsp_attributes_write(self, sender, args):
        logging.debug('handler_ble_rsp_attributes_write: %s', args)

    def handler_ble_rsp_attributes_user_write_response(self, sender, args):
        logging.debug('handler_ble_rsp_attributes_user_write_response: %s', args)

    def handler_ble_rsp_attributes_read_type(self, sender, args):
        logging.debug('handler_ble_rsp_attributes_read_type: %s', args)

    def handler_ble_evt_attclient_indicated(self, sender, args):
        logging.debug('handler_ble_evt_attclient_indicated: %s', args)

    def register_handlers(self):
        logging.debug('registering handlers...')
        self.bglib.on_timeout += self.handler_on_timeout
        self.bglib.ble_evt_gap_scan_response += self.handler_ble_evt_gap_scan_response
        self.bglib.ble_evt_connection_disconnected += self.handler_ble_evt_connection_disconnected
        self.bglib.ble_evt_connection_status += self.handler_ble_evt_connection_status
        self.bglib.ble_rsp_attributes_read += self.handler_ble_rsp_attributes_read
        self.bglib.ble_rsp_attributes_user_read_response += self.handler_ble_rsp_attributes_user_read_response
        self.bglib.ble_rsp_attributes_write += self.handler_ble_rsp_attributes_write
        self.bglib.ble_rsp_attributes_user_write_response += self.handler_ble_rsp_attributes_user_write_response
        self.bglib.ble_rsp_attributes_read_type += self.handler_ble_rsp_attributes_read_type
        self.bglib.ble_evt_attclient_indicated += self.handler_ble_evt_attclient_indicated
        self.bglib.ble_evt_attributes_value += self.handler_ble_evt_attributes_value
        self.bglib.ble_evt_attributes_user_read_request += self.handler_ble_evt_attributes_user_read_request
        self.bglib.ble_rsp_gap_set_mode +=self.handler_ble_rsp_gap_set_mode
        self.bglib.ble_rsp_connection_update +=self.handler_ble_rsp_connection_update
#----------------------------------------

class Tx(object):
    '''
    Send data from Rest interface to Ble TX service
    '''
    def __init__(self, url):
        self.json_url = url+'/data/+' # TODO This is now very smap specific
        self.jsonblob = ''
        self.data = [] # Data in byte array
        self.packetno = 0
        self.frameno = 0

    def update_json(self):
        print self.json_url
        r = None
        try:
            r = requests.get(self.json_url, timeout=4)
        except:
            pass
        if r !=None:
            if r.status_code == 200:
                self.jsonblob = str(r.text)
            else:
                logging.error('GET returned !=200 '+str(r.status_code))
        else:
            self.jsonblob = ''
        compressed = util.compress(self.jsonblob)
        b = util.get_char_array(compressed)
        self.data=[b[x:x+600] for x in xrange(0, len(b), 600)]
        #NOTE 600 bytes seems to be the maximum we can fit into a Ble characteristic

class Rx(object):
    '''
    Receive Data from Ble and send to Rest interface
    '''
    def __init__(self, url):
        self.rest = url+'/data/'
        self.packetno = 0 #packet no of our current transfer process
        self.data = [] # to store data that we receive from the client in bytes
        self.request = '' # message in unicode
        self.success = False

    def send_request(self):
        '''
        Sends a request from a BLEoT client to the actual REST Interface
        '''
        req = self.rest + self.request
        req = str(req)
        urlhard = "http://192.168.0.103:8080/data/HueBridge0/2/state/on_act?state=1"
        print "REQ "+req
        try:
            r =  requests.put(req, timeout=1)
        except:
            logging.warning('Url not valid or no connection \n'+str(sys.exc_info()[0]))
            return False
        if r.status_code == 200 or 204:
            logging.info('Successfully set new value')
            return True
        else:
            logging.warning('PUT returned !=200 or 204 '+str(r.status_code))
            return False

# gracefully exit without a big exception message if possible
# FIXME should flush our buffers here
def ctrl_c_handler(signal, frame):
    print 'BLEoTGateway shutting down'
    exit(0)
signal.signal(signal.SIGINT, ctrl_c_handler)

def main():
    parser =  argparse.ArgumentParser(description='''Starts a BLEoT Gateway 
            service on the device.''', epilog='''Note: This requires a BLED112
            dongle from Bluegiga.''')
    parser.add_argument('-p','--path', help='''Path to Bluegiga device
            (e.g., /dev/tty.usbmodem1)''',required=True)
    parser.add_argument('-u', '--url', help='''URL of RESTful interface that
            should be gatewayed''', required=True)
    parser.add_argument('-n', '--name', help='Advertising name of BLEoT Gateway',
            required=False, default='BLEoTGW')
    parser.add_argument('-d', '--debug', help='Debug level (0-4)',
            type=int, default=20, choices=[10, 20, 30, 40, 50])
    args = parser.parse_args()
    logging.basicConfig(filename='BLEoTGateway.log', level=args.debug)
    logging.info('BLEoTGateway is starting.')
    print 'BLEoTGateway is starting'
    logging.debug('Creating new BLEoTG() instance')
    adv_name_rf = util.pad_truncate(args.name, 7)
    bleotgw = BLEoTG(port_name=args.path, adv_name=adv_name_rf, url=args.url)
    bleotgw.register_handlers()
    bleotgw.setup()
    logging.info('BLEoTGateway ready to accept incoming connections.')
    print 'BLEoTGateway ready to accept incoming connections'
    while (1):
        # catch all incoming data
        bleotgw.bglib.check_activity(bleotgw.ser)
        # don't burden the CPU
        time.sleep(0.01)
        # if for some reason, we end up in standby, advertise again...
        if bleotgw.state == BLE_STATE_STANDBY:
            logging.debug('''BLEoTGateway is in standby, starting to advertise 
                    again as connectable.''')
            bleotgw.bglib.send_command(bleotgw.ser, bleotgw.bglib.ble_cmd_gap_set_mode(
                bleotgw.discoverable, bleotgw.connectable))
            bleotgw.bglib.check_activity(bleotgw.ser, 1)
            bleotgw.state = BLE_STATE_ADVERTISING
            logging.debug('BLEoTGateway is advertising again.')

if __name__ == '__main__':
    main()
