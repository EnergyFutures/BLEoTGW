#!/usr/bin/env python
# -*- coding: utf-8 -*-
import bglib, serial, time, datetime,json, array, signal, requests, os, sys, zlib

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

class Tx(object):
    '''
    Send data from Rest interface to Ble TX service
    '''
    def __init__(self):
        #self.jsonblob = json.dumps(['foo', {'bar': ('baz', None, 1.0, 2)}])
        self.json_url = "http://192.168.0.100:8080/data/+" #URL to JSON string that contains all information about the REST interface
        self.jsonblob = ''
        self.data = [] # Data in byte array
        self.packetno = 0
        self.frameno = 0

    def update_json(self):
        r = None
        try:
            r = requests.get(self.json_url, timeout=1)
        except:
            pass
        if r !=None:
            if r.status_code == 200:
                self.jsonblob = str(r.text)
            else:
                print "GET returned !=200 "+str(r.status_code)
        else:
            self.jsonblob = ''
        compressed = zlib.compress(self.jsonblob, 9)
        b = array.array("B", compressed)
        self.data=[b[x:x+600] for x in xrange(0, len(b), 600)] #NOTE 600 bytes seems to be the maximum we can fit into a Ble packet
        if DEBUG:
            print "LENGTH"
            print len(b)
            for item in self.data:
                print item

class Rx(object):
    '''
    Receive Data from Ble and send to Rest interface
    '''
    def __init__(self):
        self.rest = "http://192.168.0.102:8080/data/"
        self.packetno = 0 #packet no of our current transfer process
        self.data = [] # to store data that we receive from the client in bytes
        self.request = '' # message in unicode
        self.success = False

    def send_request(self):
        '''
        Sends a request from a Ble client to the actual Rest Interface
        '''
        req = self.rest + self.request
        try:
            r =  requests.put(req, timeout=1)
        except:
            print "Url not valid or no connection \n"+str(sys.exc_info()[0])
            return False
        if r.status_code == 200 or 204:
            print "Successfully set new value"
            return True
        else:
            print "PUT returned !=200 or 204 "+str(r.status_code)
            return False


class BleCon(object):
    def __init__(self):
        self.address = None #Bluetooth ID of the client
        self.tx = Tx()
        self.rx = Rx()

class Ble(object):
    def __init__(self, port_name="/dev/tty.usbmodem1", baud_rate=38400):
        self.bglib = bglib.BGLib()
        self.port_name = port_name
        self.baud_rate = baud_rate
        self.packet_mode = False
        self.major = 0x0001
        self.minor = 0x0001
        self.adv_min = 48
        self.adv_max = 64
        self.serial = None
        self.state = None
        # 0=gap_non_discoverable, 1=gap_limited_discoverable,
        #2=gap_general_discoverable, 3=gap_broadcast, 4=gap_user_data
        self.discoverable = 4
        #0=gap_non_connectable, 1=gap_directed_connectable,
        #2=gap_undirected_connectable, 3=gap_scannable_non_connectable
        self.connectable = 2
        self.known_clients = {}
        self.blacklisted_clients = {}
        self.active_client = None
        #self.uuid = [0xe4, 0xba, 0x94, 0xc3, 0xc9, 0xb7, 0xcd, 0xb0, 0x9b, 0x48,
        #        0x7a, 0x43, 0x8a, 0xe5, 0x5a, 0x19]
        # build custom advertisement data
        # default BLE stack value: 0201061107e4ba94c3c9b7cdb09b487a438ae55a19
        #self.adv_data = [
        #        0x06, # field length
        #        0x08, # BGLIB_GAP_AD_TYPE_LOCALNAME_COMPLETE --> field type 0x08=shortname
        #        0x42, 0x6C, 0x65, 0x50, 0x69,
        #        0x02, # field length
        #        0x01, # BGLIB_GAP_AD_TYPE_FLAGS --> field type
        #        0x06, # data (0x02 | 0x04 = 0x06, general discoverable + BLE only, no BR+EDR)
        #        0x07, # field length
        #        0xFF, # BGLIB_GAP_AD_TYPE_SERVICES_128BIT_ALL --> field type
        #        0xDD, 0xDD, 0xd, 0xc, 0xb, 0xa# 16 byte UUID placeholders
        #        #TODO 0xff as type and ID is: ABCD
        #        ]

        self.adv_data = [
                0x0B, # field length
                0x08, # BGLIB_GAP_AD_TYPE_LOCALNAME_COMPLETE --> field type 0x08=shortname
                0x49, 0x54, 0x55, 0x41, 0x43, 0x54, 0x55, 0x41, 0x54, 0x31,
                0x02, # field length
                0x01, # BGLIB_GAP_AD_TYPE_FLAGS --> field type
                0x06, # data (0x02 | 0x04 = 0x06, general discoverable + BLE only, no BR+EDR)
                0x0B, # field length
                0xFF, # BGLIB_GAP_AD_TYPE_SERVICES_128BIT_ALL --> field type
                0xDD, 0xDD, 0x3B, 0x34, 0x44, 0x32, 0x31, 0x04, 0x00, 0x02 # DDDD_HEADER_4D21_TYPE_COORDINATES_MISC
                #TODO 0xff as type and ID is: ABCD
                ]
        

        # set UUID specifically
        #self.adv_data[9:25] = self.uuid[0:16]
        
        # build custom scan response data
        # default BLE stack value: 140942474c69622055314131502033382e344e4657
        # NOTE there is no need for scan response data, this is the second packet that we could send when a master scans
        # self.sr_data = [
        #         0x06, # field length
        #         0x09, # BGLIB_GAP_AD_TYPE_LOCALNAME_COMPLETE --> field type 0x08=shortname
        #         0x42, 0x6C, 0x65, 0x50, 0x69
        #         ]

    def setup(self):
        self.bglib.packet_mode = self.packet_mode
        self.ser = serial.Serial(port=self.port_name, baudrate=self.baud_rate, timeout=1)
        # create serial port object and flush buffers
        print 'flushing input'
        self.ser.flushInput()
        print 'flushing output'
        self.ser.flushOutput()

        # disconnect if we are connected already
        print 'disconnecting'
        self.bglib.send_command(self.ser, self.bglib.ble_cmd_connection_disconnect(0))
        print 'checking activity'
        self.bglib.check_activity(self.ser, 1)

        # stop advertising if we are advertising already
        # 0 gap_non_discoverable, 0 gap_non_connectable
        print 'stop advertising'
        self.bglib.send_command(self.ser, self.bglib.ble_cmd_gap_set_mode(0, 0))
        self.bglib.check_activity(self.ser, 1)
    
        # stop scanning if we are scanning already
        # This command ends the current GAP discovery procedure and stop the scanning
        # of advertising devices
        print 'stop scanning'
        self.bglib.send_command(self.ser, self.bglib.ble_cmd_gap_end_procedure())
        self.bglib.check_activity(self.ser, 1)

        # now we musst be in STANDBY state
        self.state = BLE_STATE_STANDBY
        

        print 'set advertisement (min/max interval + all three ad channels)'
        # This is just to tweak channels, radio...
        self.bglib.send_command(self.ser, self.bglib.ble_cmd_gap_set_adv_parameters(
            int(self.adv_min * 0.625), int(self.adv_max * 0.625), 7))
        self.bglib.check_activity(self.ser, 1)

        # 4 means custom user data in advertisment packet
        if self.discoverable == 4:
            print 'set user defined advertisement packet'
            self.bglib.send_command(self.ser, self.bglib.ble_cmd_gap_set_adv_data(0, self.adv_data))
            self.bglib.check_activity(self.ser, 1)

            # print 'set local name (scan response packet)'
            # self.bglib.send_command(self.ser, self.bglib.ble_cmd_gap_set_adv_data(1,
            #     self.sr_data))
            # self.bglib.check_activity(self.ser, 1)

        # start advertising as discoverable with user data (4) and connectable (2)
        # ibeacon was 0x84, 0x03
        print "Entering advertisement mode..."
        self.bglib.send_command(self.ser, self.bglib.ble_cmd_gap_set_mode(self.discoverable, self.connectable))
        self.bglib.check_activity(self.ser, 1)

        # set state to advertising
        self.state = BLE_STATE_ADVERTISING 
        #value = [ 0x00]
        #ble.send_command(ser, ble.ble_cmd_attributes_write(21,0,value))
        #ble.check_activity(ser, 1)

        # FIXME is a timeout really serious? can we do anything here except to notice that we had a timeout?
        # handler to notify of an API parser timeout condition


#------------------------------------------------------------------------------
# Event Handlers
#------------------------------------------------------------------------------
    def handler_on_timeout(self, sender, args):
        '''
        Gets called when we send a command but we don't get a response back e.g.
        '''
        if DEBUG:
            print "handler_on_timeout"
            print args
        # might want to try the following lines to reset, though it probably
        # wouldn't work at this point if it's already timed out:
        self.bglib.send_command(self.ser, self.bglib.ble_cmd_system_reset(0))
        self.state = BLE_STATE_STANDBY

    # FIXME not needed as we are not in enhanced broadcasting mode,
    # This will never get called
    def handler_ble_evt_gap_scan_response(self, sender, args):
        """
        Handler to print scan responses with a timestamp (this only works when
        discoverable mode is set to enhanced broadcasting 0x80/0x84)
        """
        if DEBUG:
            print "ble_evt_gap_scan_response"
            print args
            t = datetime.datetime.now()
            disp_list = []
            disp_list.append("%ld.%03ld" % (time.mktime(t.timetuple()), t.microsecond/1000))
            disp_list.append("%d" % args["rssi"])
            disp_list.append("%d" % args["packet_type"])
            disp_list.append("%s" % ''.join(['%02X' % b for b in args["sender"][::-1]]))
            disp_list.append("%d" % args["address_type"])
            disp_list.append("%d" % args["bond"])
            disp_list.append("%s" % ''.join(['%02X' % b for b in args["data"]]))
            print ' '.join(disp_list)

    def handler_ble_evt_connection_status(self, sender, args):
        '''
        This gets called when a client has just been connected to us.
        We need to check if we already know it, potentially check if it is blacklisted etc...
        Then we set it as our active client.
        '''
        if DEBUG:
            print "ble_evt_connection_status"
            print args

        # check if connection status flags are 5 (bit0=connection_connected + bit2=connection completed)
        if (args['flags'] & 0x05) == 0x05:
            self.state = BLE_STATE_CONNECTED_SLAVE
            # now let's keep the clients address as its unique ID and set it as active
            client_add = tuple(args['address'])
            connection = args['connection']
            if client_add in self.blacklisted_clients:
                self.bgli.send_command(self.ser, self.bglib.ble_cmd_connection_disconnect(connection))
            else:
                if client_add not in self.known_clients:
                    con = BleCon() #Create a new connection object for the new client
                    self.known_clients[client_add] = con
                else:
                    con = self.known_clients[client_add]
                self.active_client = con
                if self.active_client.tx.packetno == 0:
                    self.active_client.tx.update_json() #lets keep our state regularly updated
            if DEBUG:
                print "Requesting to upgrade connection"
            self.bglib.send_command(self.ser, self.bglib.ble_cmd_connection_update(connection, self.adv_min * 0.625, self.adv_max * 0.625, 25, 6000))
        else:
            print "Connection was not correctly established"

    def handler_ble_evt_connection_disconnected(self, sender, args):
        '''
        A client has just disconnected from us
        '''
        if DEBUG:
            print "ble_evt_connection_disconnected"
            print args
            if args['reason'] == 0x213:
                print "User on the remote device terminated the connection"

        # Remove the active client from our app (even this should not be necessary)
        self.active_client = None
        # We need to advertise ourselves again as a slave
        self.bglib.send_command(self.ser, self.bglib.ble_cmd_gap_set_mode(self.discoverable, self.connectable))

        # we can now set state to advertise again
        self.state = BLE_STATE_ADVERTISING

    def handler_ble_rsp_gap_set_mode(self, senser, args):
        '''
        GAP mode has been set in response to
        self.bglib.ble_cmd_gap_set_mode
        '''
        print "ble_rsp_gap_set_mode"
        print args
        if args["result"] != 0:
            print "ble_rsp_gap_set_mode FAILED\n Re-running setup()"
            self.setup()
        else:
            print "GAP mode sucessfully set"

    def handler_ble_evt_attributes_value(self, sender, args):
        '''
        Gets called when an attribute has been written by a client
        {'connection': 0, 'handle': 25, 'reason': 2, 'value': [1], 'offset': 0}
        '''
        if DEBUG:
            print "ble_evt_attributes_value"
            print args
        # RX characteristic TODO is it good to hardcode this?
        if args['handle'] == 25:
            value = args['value']
            print "client is writting a command"
            if self.active_client.rx.packetno == 0:
                # cleanout old data TODO might be good to keep track of past commands?
                self.active_client.rx.data = []
            for char in value:
                self.active_client.rx.data.append(char)
            if DEBUG:
                print self.active_client.rx.data
            self.active_client.rx.packetno += 1
            #if len(value) < 22:
            if value[-1] == 0x0A: # check for new line character at the end
                print "last packet received"
                # resetting packet no
                self.active_client.rx.packetno = 0
                # NOTE Now, we need to parse what we have received and then execute it
                for char in self.active_client.rx.data[0:-1]: #process all but last character
                    print char
                    print str(unichr (char))
                    self.active_client.rx.request = self.active_client.rx.request + str(unichr(char))
                print self.active_client.rx.request
                self.active_client.rx.success = self.active_client.rx.send_request()
                self.active_client.rx_message = ''



    def handler_ble_rsp_attributes_read(self, sender, args):
        if DEBUG:
            print "handler_ble_rsp_attributes_read"
            print args

    def handler_ble_rsp_attributes_user_read_response(self, sender, args):
        if DEBUG:
            print "ble_rsp_attributes_user_read_response"
            print args

    def handler_ble_rsp_attributes_write(self, sender, args):
        if DEBUG:
            print "ble_rsp_attributes_write"
            print args

    def handler_ble_rsp_attributes_user_write_response(self, sender, args):
        if DEBUG:
            print "ble_rsp_attributes_user_write_response"
            print args

    def handler_ble_rsp_attributes_read_type(self, sender, args):
        if DEBUG:
            print "ble_rsp_attributes_read_type"
            print args

    def handler_ble_evt_attclient_indicated(self, sender, args):
        if DEBUG:
            print "ble_evt_attclient_indicated"
            print args

    def handler_ble_evt_attributes_user_read_request(self, sender, args):
        '''
        This is called whenever a client reads an attribute that has the user type 
        enabled. We then serve the data dynamically. Each packet payload is 20 Bytes.
        Whenever a client is receiving 20 bytes, the client needs to issue another
        read request on the same atttribute as long as all the data is received
        (This can be concludes when we receive less than 20 bytes)
        'connection': connection, 'handle': handle, 'offset': offset, 'maxsize': maxsize
        '''
        if DEBUG:
            print "ble_evt_attributes_user_read_request"
            print args
        client_con = args['connection'] # --> we should not care about connection No for identification
        # as we only can have one single client connected at the same time
        # for feedback if previous write has been suceeded
        if self.active_client !=None: # this can happen if there for some reason old data in buffer
            if args['handle'] == 25:
                if self.active_client.rx.success:
                    value = [0x01]
                else:
                    value = [0x00]
                self.bglib.send_command(self.ser, self.bglib.ble_cmd_attributes_user_read_response(client_con, 0, value))
            if args['handle'] == 21:
                if DEBUG:
                    print "Packet No"
                    print self.active_client.tx.packetno
                value = get_byte_packet(self.active_client.tx.packetno, self.active_client.tx.frameno, self.active_client.tx.data)
                if len(value) < 22:
                    self.active_client.tx.packetno = 0
                    if self.active_client.tx.frameno == (len(self.active_client.tx.data)-1):
                        print "FINITO"
                        self.active_client.tx.frameno = 0 # we have actually transimitted all data
                    else:
                        self.active_client.tx.frameno+=1

                else:
                    self.active_client.tx.packetno+=1
                if DEBUG:
                    print value
                
                self.bglib.send_command(self.ser, self.bglib.ble_cmd_attributes_user_read_response(client_con, 0, value))
        # this should not happen, only if we have some old data, let's reset
        else:
            self.bglib.send_command(self.ser, self.bglib.ble_cmd_system_reset(0))

    def handler_ble_rsp_connection_update(self, sender, args):
        '''
            Gets called as a result of us trying to upgrade an existing connection.
        '''
        if DEBUG:
            print "ble_rsp_connection_update"
            print args
        if args['result'] != 0:
            self.bglib.send_command(self.ser, self.bglib.ble_cmd_system_reset(0))
            self.state = BLE_STATE_STANDBY



    def handler_ble_evt_attributes_status(self, sender, args):
        '''
            Gets called when a client enables notification or indication
        '''
        if DEBUG:
            print "ble_evt_attributes_status"
            print args


    def register_handlers(self):
        if DEBUG:
            print "registering handlers"
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
        #self.bglib.ble_evt_attributes_status += self.handler_ble_evt_attributes_status FIXME add for indication

#----------------------------------------

# gracefully exit without a big exception message if possible
# FIXME should flush our buffers here
def ctrl_c_handler(signal, frame):
    print 'Goodbye!'
    exit(0)
signal.signal(signal.SIGINT, ctrl_c_handler)


def get_byte_packet(packetno, frameno, databytes):
    '''
    Returns a subarray that fits into a bluetooth packet
    '''
    subarray = databytes[frameno][packetno*22:(packetno+1)*22] #is empty as soon as we request more
    return subarray

def main():
    ble = Ble()
    ble.register_handlers()
    ble.setup()
    print 'entering while loop to listen for incoming packages'
    while (1):
        # catch all incoming data
        ble.bglib.check_activity(ble.ser)
        # don't burden the CPU
        time.sleep(0.01)
        # if for some reason, we end up in standby, advertise again...
        if ble.state == BLE_STATE_STANDBY:
            if DEBUG:
                print "Somehow we are in standby..entering advertisement mode again..."
            ble.bglib.send_command(ble.ser, ble.bglib.ble_cmd_gap_set_mode(ble.discoverable, ble.connectable))
            ble.bglib.check_activity(ble.ser, 1)
            ble.state = BLE_STATE_ADVERTISING

if __name__ == '__main__':
    main()
