# BLEoTGW
Turns a Raspberry Pi (or any other general computing platform) into a Rest-BLE Gateway using the Bluegiga Bled112 dongle.
The REST interface can then be accessed using smart devices (smart phones, tablets, laptops) with Bluetooth capabilities.
Access to any existing network infrastructure is hence not necessary and users are authorized purely by their locality.

## Usage:
BLEoTGW.py [-h] -p PATH -u URL [-n NAME] [-d {10,20,30,40,50}]

Starts a BLEoT Gateway service on the device.

optional arguments:
  -h, --help            show this help message and exit
  -p PATH, --path PATH  Path to Bluegiga device (e.g., /dev/tty.usbmodem1)
  -u URL, --url URL     URL of RESTful interface that should be gatewayed
  -n NAME, --name NAME  Advertising name of BLEoT Gateway
  -d {10,20,30,40,50}, --debug {10,20,30,40,50} Debug level (0-4)

Note: This requires a BLED112 dongle from Bluegiga.

