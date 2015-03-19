#!/usr/bin/env python
# -*- coding: utf-8 -*-
import bglib, serial, time, datetime,json, array, signal, zlib

#----------------------------------------
# BLE state machine definitions
#----------------------------------------
BLE_STATE_STANDBY = 0
BLE_STATE_SCANNING = 1
BLE_STATE_ADVERTISING = 2
BLE_STATE_CONNECTING = 3
BLE_STATE_CONNECTED_MASTER = 4
BLE_STATE_CONNECTED_SLAVE = 5
BLE_STATE_FINDING_SERVICES = 6
BLE_STATE_FINDING_ATTRIBUTES = 7
STATE_LISTENING_MEASUREMENTS = 8
#----------------------------------------
DEBUG = True


class Tx():
    '''
    Send data from Rest interface to Ble
    '''
    def __init__(self):
        self.jsonblob = json.dumps(['foo', {'bar': ('baz', None, 1.0, 2)}])
        self.jsonbytes = array.array("B", self.jsonblob)

class Rx():
    '''
    Receive Data from Ble and send to Rest
    '''
    def __init__(self):
        self.jsonblob = None


class BleCon():
    def __init__(self):
        self.packetno = 0 #packet no of our current transfer process
        self.address = None #ID of the client
        self.connection = None

class Ble():
    def __init__(self, port_name="/dev/tty.usbmodem17", baud_rate=38400):
        self.bglib = bglib.BGLib()
        self.port_name = port_name
        self.baud_rate = baud_rate
        self.packet_mode = False
        self.uuid = [ 0xe2, 0xc5, 0x6d, 0xb5, 0xdf, 0xfb, 0x48, 0xd2, 0xb0, 0x60,
                0xd0, 0xf5, 0xa7, 0x10, 0x96, 0xe0 ]
        self.major = 0x0001
        self.minor = 0x0001
        self.adv_min = 90
        self.adv_max = 110
        self.serial = None
        self.state = None
        self.counter = 0

        self.att_handle_start = 0
        self.att_handle_end = 0
        self.att_handle_measurement = 0
        self.att_handle_measurement_ccc = 0
        # 0=gap_non_discoverable, 1=gap_limited_discoverable,
        #2=gap_general_discoverable, 3=gap_broadcast, 4=gap_user_data
        self.discoverable = 4
        #0=gap_non_connectable, 1=gap_directed_connectable,
        #2=gap_undirected_connectable, 3=gap_scannable_non_connectable
        self.connectable = 2
        self.known_clients = {}
        self.known_periphals = []
        self.active_client = None
        self.tx = Tx()
        self.rx = Rx()
        self.message = ''
        self.zipped = []

        # somehow our own service does not get advertised
        self.uuid_tx = [
                0x00, 0x00, 0xFF, 0xA1, 0x34, 0x44, 0x32, 0x31, 0x34, 0x54, 0x48, 0x45, 0x57, 0x49, 0x4E, 0x21]
        self.uuid_service = [0x28, 0x00] # 0x2800
        self.uuid_client_characteristic_configuration = [0x29, 0x02] # 0x2902

        # this is our own manufactor version 0xDD, 0xDD...
        self.uuid_blepi_service = [255, 221, 221, 59, 52, 68, 50, 49, 4, 0, 2]
        # This is the firmware service
        #self.uuid_blepi_service = [
        #        68, 120, 198, 227, 41, 9, 67, 104, 173, 235,
        #        69, 238, 34, 103, 214, 57]
        #self.uuid_blepi_service = [0xA6, 0x32, 0x25, 0x21, 0xEB, 0x79, 0x4B, 0x9F, 0x91, 0x52, 0x19, 0xDA, 0xA4, 0x87, 0x04, 0x18]
        self.uuid_blepi_characteristic = [
                0x5d, 0x72, 0x23, 0xc3, 0x0f, 0x6a, 0x4c, 0x64, 0xb1, 0xbe,
                0x40, 0xb5, 0x18, 0x49, 0x99, 0x23]
        self.uuid_blepi_char_rx = [0x00, 0x00,0xFF,0x03,0x34,0x44,0x32,0x31, 0x34,0x54, 0x48, 0x45, 0x57, 0x49, 0x4E, 0x21]
        self.uuid_blepi_char_tx = [0x00, 0x00, 0xFF, 0x03, 0x34, 0x44, 0x32, 0x31, 0x34, 0x54, 0x48, 0x45, 0x57, 0x49, 0x4E, 0x21]

        self.uuid_blepi_characteristic = self.uuid_blepi_char_tx

    def setup(self):
        self.bglib.packet_mode = self.packet_mode
        # add try except FIXME
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
       
        # set scan parameters
        self.bglib.send_command(self.ser, self.bglib.ble_cmd_gap_set_scan_parameters(0xC8, 0xC8, 1))
        self.bglib.check_activity(self.ser, 1)

        # start scanning now
        print "Scanning for BLE peripherals..."
        self.scanstart = time.time()
        self.bglib.send_command(self.ser, self.bglib.ble_cmd_gap_discover(1))
        self.bglib.check_activity(self.ser, 1)
       

        self.state = BLE_STATE_SCANNING

#----------------------------------------
# Event Handlers
#----------------------------------------
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
        # pull all advertised service info from ad packet
        ad_services = []
        this_field = []
        bytes_left = 0
        for b in args['data']:
            if bytes_left == 0:
                bytes_left = b
                this_field = []
            else:
                this_field.append(b)
                bytes_left = bytes_left - 1
                if bytes_left == 0:
                    print this_field
                    if this_field[0] == 0xFF: #own manufactor ID
                        ad_services = this_field
                        print "FOUND"
                    else:
                        print "NOSING"

        # NOTE: ad_services is built in normal byte order, reversed from that reported in the ad packet
        print "AD SERVICES"
        print ad_services
        print ""
        if self.uuid_blepi_service == ad_services:
            if not args['sender'] in self.known_periphals:
                self.known_periphals.append(args['sender']) # FIXME make Dic
                #print "%s" % ':'.join(['%02X' % b for b in args['sender'][::-1]])

                # connect to this device using very fast connection parameters (7.5ms - 15ms range)
                self.bglib.send_command(self.ser, self.bglib.ble_cmd_gap_connect_direct(args['sender'], args['address_type'], 0x06, 0x0C, 0x100, 0))
                self.bglib.check_activity(self.ser, 1)
                self.state = BLE_STATE_CONNECTING


    def handler_ble_evt_connection_status(self, sender, args):
        if DEBUG:
            print "ble_evt_connection_status"
            print args
        if (args['flags'] & 0x05) == 0x05:
            self.state = BLE_STATE_CONNECTED_MASTER
            # connected, now perform service discovery
            print "Connected to %s" % ':'.join(['%02X' % b for b in args['address'][::-1]])
            self.connection_handle = args['connection']
            # NOTE: BGLib command expects little-endian UUID byte order, so it must be reversed for using
            # NOTE2: must be put inside "list()" so that it is once again iterable
            self.bglib.send_command(self.ser,
                    self.bglib.ble_cmd_attclient_read_by_group_type(
                        args['connection'], 0x0001, 0xFFFF,
                        list(reversed(self.uuid_service))))
            self.bglib.check_activity(self.ser, 1)
            self.state = BLE_STATE_FINDING_SERVICES


    def handler_ble_rsp_attclient_read_by_group_type(self, sender, args):
        if DEBUG:
            print "handler_ble_rsp_attclient_read_by_group_type"
            print args

    def handler_ble_evt_attclient_group_found(self, sender, args):
        if DEBUG:
            print "ble_evt_attclient_group_found"
            print args

        # Now, lets check if this periphal contains our BlePi service
        # found "service" attribute groups (UUID=0x2800),
        # check for thermometer service (0x f90ea017-f673-45b8-b00b-16a088a2ed61)
        # NOTE: args['uuid'] contains little-endian UUID byte order directly
        # from the API response, so it must be reversed for comparison
        if args['uuid'] == list(reversed(self.uuid_tx)): # FIXME his needs to be the UUID of our TX service
            print "Found attribute group for ADC service: start=%d, end=%d" % (args['start'], args['end'])
            self.att_handle_start = args['start']
            self.att_handle_end = args['end']

    def handler_ble_evt_attclient_find_information_found(self, sender, args):
        if DEBUG:
            print "handler_ble_evt_attclient_find_information_found"
            print args
        # check for thermometer measurement characteristic
        # NOTE: args['uuid'] contains little-endian UUID byte order directly
        # from the API response, so it must be reversed for comparison
        if args['uuid'] == list(reversed(self.uuid_blepi_characteristic)):
            print "Found attribute for ADC measurement: handle=%d" % args['chrhandle']
            self.att_handle_measurement = args['chrhandle']
            # NOTE this means we are at our TX characteristic

        # check for subsequent client characteristic configuration
        # NOTE: args['uuid'] contains little-endian UUID byte order directly from the API response, so it must be reversed for comparison
        elif args['uuid'] == list(reversed(self.uuid_client_characteristic_configuration)) and self.att_handle_measurement > 0:
            print "Found attribute w/UUID=0x2902: handle=%d" % args['chrhandle']
            self.att_handle_measurement_ccc = args['chrhandle']
            
            
    def handler_ble_evt_attclient_procedure_completed(self, sender, args):
        if DEBUG:
            print "handler_ble_evt_attclient_procedure_completed"
            print args
        # check if we just finished searching for services
        print self.connection_handle
        print self.att_handle_start
        print self.att_handle_end
        print self.att_handle_measurement
        print self.att_handle_measurement_ccc
        if self.state == BLE_STATE_FINDING_SERVICES:
            if self.att_handle_end > 0:
                print "Found ADC service"

                # found the ADC service, so now search for the attributes inside
                self.state = BLE_STATE_FINDING_ATTRIBUTES
                self.bglib.send_command(self.ser,
                        self.bglib.ble_cmd_attclient_find_information(
                            self.connection_handle, self.att_handle_start,
                            self.att_handle_end))
                self.bglib.check_activity(self.ser, 1)
            else:
                print "Could not find ADC service"

        # check if we just finished searching for attributes within the ADC service
        elif self.state == BLE_STATE_FINDING_ATTRIBUTES:
            if self.att_handle_measurement > 0:
                self.scanstop = time.time()
                print "Scan Start, Stop, Delta"
                print str(self.scanstart) + " , " + str(self.scanstop) + " , " + str(self.scanstop-self.scanstart)
                print "reading"
                self.starttime = time.time()
                self.state = STATE_LISTENING_MEASUREMENTS
                self.bglib.send_command(self.ser,
                        self.bglib.ble_cmd_attclient_read_by_handle(
                            self.connection_handle, self.att_handle_measurement))
                self.bglib.check_activity(self.ser, 1)

            if self.att_handle_measurement_ccc > 0:
                print "Found ADC measurement attribute service"
                # found the measurement + client characteristic configuration, so enable notifications
                # (this is done by writing 0x0001 to the client characteristic configuration attribute)
                self.state = STATE_LISTENING_MEASUREMENTS
                sellf.bglib.send_command(self.ser, self.bglib.ble_cmd_attclient_attribute_write(self.connection_handle, self.att_handle_measurement_ccc, [0x01, 0x00]))
                self.bglib.check_activity(self.ser, 1)
            else:
                pass
                #print "Could not find ADC measurement attribute"
        
    def handler_ble_evt_attclient_attribute_value(self, sender, args):
        if DEBUG:
            print "ble_evt_attclient_attribute_value"
            print args
        for char in args['value']:
            self.zipped.append(char)
            #self.message.join(chr(char))
            self.message = self.message+unichr(char)
        if len(args['value']) == 22:
            self.bglib.send_command(self.ser,
                    self.bglib.ble_cmd_attclient_read_by_handle(
                        self.connection_handle, self.att_handle_measurement))
            self.bglib.check_activity(self.ser, 1)
        else:
            if self.counter == 1:
                self.stoptime = time.time()
                print "Started, Finished, Delta"
                print str(self.starttime) + ","+ str(self.stoptime) + "," + str (self.stoptime-self.starttime)
                #print str(zlib.decompress(self.message, 15 + 32))
                print 'message'
                print self.message
                print self.zipped
                print len(self.zipped)
                self.bglib.send_command(self.ser, self.bglib.ble_cmd_attclient_attribute_write(self.connection_handle, 24, [0,1]))
                self.bglib.send_command(self.ser, self.bglib.ble_cmd_connection_disconnect(self.connection_handle))
            if self.counter == 0:
                self.bglib.send_command(self.ser,
                    self.bglib.ble_cmd_attclient_read_by_handle(
                        self.connection_handle, self.att_handle_measurement))
                self.counter +=1

    def handler_ble_evt_connection_disconnected(self, sender, args):
        '''
        A client has just disconnected from us
        '''
        if DEBUG:
            print "ble_evt_connection_disconnected"
            print args
        # Remove the active client from our app (even this should not be necessary)
        self.active_client = None
        # We need to advertise ourselves again as a slave
        self.bglib.send_command(self.ser, self.bglib.ble_cmd_gap_set_mode(
            self.discoverable, self.connectable))

        # we can now advertise us again
        self.state = BLE_STATE_STANDBY
    
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

    def handler_ble_evt_attributes_value(self, sender, args):
        if DEBUG:
            print "ble_evt_attributes_value"
            print args

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
        '''
        if DEBUG:
            print "ble_evt_attributes_user_read_request"
            print args
        client_con = args['connection'] # --> we should not care about connection No for identification
        # as we only can have one single client connected at the same time
        con = self.active_client
        if con != None:
            if DEBUG:
                print "Packet No."
                print con.packetno
            # 'connection': connection, 'handle': handle, 'offset': offset, 'maxsize': maxsize })
            value = get_byte_packet(con.packetno, self.tx.jsonbytes)
            if len(value) < 20:
               con.packetno = 0
            else:
                con.packetno = con.packetno +1
            if DEBUG:
                print value
            self.bglib.send_command(self.ser, self.bglib.ble_cmd_attributes_user_read_response(client_con, 0, value))
        # this should not happen, only if we have some old data, let's reset
        else:
            self.bglib.send_command(self.ser, self.bglib.ble_cmd_system_reset(0))


    def handler_ble_rsp_connection_update(self, sender, args):
        if DEBUG:
            print "ble_rsp_connection_update"
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
        self.bglib.ble_evt_attclient_group_found +=self.handler_ble_evt_attclient_group_found
        self.bglib.ble_rsp_attclient_read_by_group_type += self.handler_ble_rsp_attclient_read_by_group_type
        self.bglib.ble_evt_attclient_find_information_found += self.handler_ble_evt_attclient_find_information_found
        self.bglib.ble_evt_attclient_procedure_completed +=self.handler_ble_evt_attclient_procedure_completed
        self.bglib.ble_evt_attclient_attribute_value +=self.handler_ble_evt_attclient_attribute_value

#----------------------------------------

# gracefully exit without a big exception message if possible
# FIXME should flush our buffers here
def ctrl_c_handler(signal, frame):
    print 'Goodbye!'
    exit(0)
signal.signal(signal.SIGINT, ctrl_c_handler)


def get_byte_packet(packetno, databytes):
    subarray = databytes[packetno*20:(packetno+1)*20]
    return subarray

def main():
    ble = Ble()
    ble.register_handlers()
    ble.setup()
    print 'entering while loop to scan for ble devices'
    while (1):
        # catch all incoming data
        ble.bglib.check_activity(ble.ser)
        # don't burden the CPU
        time.sleep(0.01)
if __name__ == '__main__':
    main()
