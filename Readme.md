# snapshot rotator for ESXi or vSphere

this script aims to keep a defined number of snapshots.  
it will delete the oldest ones and create new ones as necessary.  

USE WITH CAUTION

```
usage: snapshot-rotator.py [-h] -s HOST [-o PORT] -u USER [-p PASSWORD] [-k KEEP] [--verbose]

optional arguments:
  -h, --help            show this help message and exit
  -s HOST, --host HOST  Remote host to connect to
  -o PORT, --port PORT  Port to connect on (default: 443)
  -u USER, --user USER  User name to use when connecting to host
  -p PASSWORD, --password PASSWORD
                        Password to use when connecting to host
  -k KEEP, --keep KEEP  How many snapshots to keep (default: 3)
  --verbose, -v
  ```
