import bglib, serial, time, datetime,json, array

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

class Ble():
    def __init__(self, port_name="/dev/tty.usbmodem1", baud_rate=38400):
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
        # 0=gap_non_discoverable, 1=gap_limited_discoverable,
        #2=gap_general_discoverable, 3=gap_broadcast, 4=gap_user_data
        self.discoverable = 4
        #0=gap_non_connectable, 1=gap_directed_connectable,
        #2=gap_undirected_connectable, 3=gap_scannable_non_connectable
        self.connectable = 2
        self.known_clients = {}
        self.active_client = None
        self.tx = Tx()
        self.rx = Rx()

        # build main ad packet
        self.ibeacon_adv = [ 0x02, 0x01, 0x06, 0x1a, 0xff, 0x4c, 0x00, 0x02, 0x15,
                    0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,
                    self.major & 0xFF, self.major >> 8,
                    self.minor & 0xFF, self.minor >> 8,
                    0xC6 ]

        # set UUID specifically
        self.ibeacon_adv[9:25] = self.uuid[0:16]

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
        self.bglib.send_command(self.ser, self.bglib.ble_cmd_gap_set_adv_parameters(
            int(self.adv_min * 0.625), int(self.adv_max * 0.625), 7))
        self.bglib.check_activity(self.ser, 1)

        print 'set beacon data (advertisement packet'
        self.bglib.send_command(self.ser, self.bglib.ble_cmd_gap_set_adv_data(0, self.ibeacon_adv))
        self.bglib.check_activity(self.ser, 1)

        print 'set local name (scan response packet)'
        self.bglib.send_command(self.ser, self.bglib.ble_cmd_gap_set_adv_data(1,
            [ 0x09, 0x09, 0x50, 0x69, 0x42, 0x65, 0x61, 0x63, 0x6f, 0x6e ]))
        self.bglib.check_activity(self.ser, 1)

        # start advertising as discoverable with user data (4) and connectable (2)
        # FIXME Why not 2 and 2???
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

    def handler_ble_evt_connection_status(self, sender, args):
        if DEBUG:
            print "ble_evt_connection_status"
            print args
        self.state = BLE_STATE_CONNECTED_SLAVE

        # now let's keep the clients address as its unique ID and set it as active
        client_add = tuple(args['address'])
        if client_add not in self.known_clients:
            con = BleCon()
            self.known_clients[client_add] = con
        else:
            con = self.known_clients[client_add]
        self.active_client = con

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
        self.bglib.send_command(self.ser, self.bglib.ble_cmd_gap_set_mode(self.discoverable, self.connectable))

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
        Whenever a client is receiving 20 bythes, the client needs to issue another
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
#----------------------------------------

def get_byte_packet(packetno, databytes):
    subarray = databytes[packetno*20:(packetno+1)*20]
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
