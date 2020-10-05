#!/usr/bin/env python
from pyVim import connect
from pyVim.connect import SmartConnect, Disconnect
from pyVmomi import vim
from pyVim.task import WaitForTask

import argparse
import atexit
import getpass
import ssl
import coloredlogs, logging

from datetime import date, datetime

argparser = argparse.ArgumentParser()
argparser.add_argument('-s', '--host', required=True, action='store',
    help='Remote host to connect to')
argparser.add_argument('-o', '--port', type=int, default=443, action='store',
    help='Port to connect on (default: %(default)s)')
argparser.add_argument('-u', '--user', required=True, action='store',
    help='User name to use when connecting to host')
argparser.add_argument('-p', '--password', required=False, action='store',
    help='Password to use when connecting to host')
argparser.add_argument('-k', '--keep', type=int, default=3, action='store',
    help='How many snapshots to keep (default: %(default)s)')
argparser.add_argument("--verbose", "-v", action='count', default=0)
args = argparser.parse_args()

logger = logging.getLogger(__name__)
levels = [logging.WARNING, logging.INFO, logging.DEBUG]
level = levels[min(len(levels)-1,args.verbose)]
coloredlogs.install(level=level)

def main():
    if args.password:
        password = args.password
    else:
        password = getpass.getpass(prompt='Enter password for host %s and '
            'user %s: ' % (args.host,args.user))

    context = None
    if hasattr(ssl, '_create_unverified_context'):
        context = ssl._create_unverified_context()
    try:
        si = SmartConnect(host=args.host,
            user=args.user,
            pwd=password,
            port=int(args.port),
            sslContext=context)
    except vim.fault.InvalidLogin as msg:
        print("failed logging in: ", msg.msg)
        return -1

    if not si:
        logger.critical("Could not connect to the specified host using specified "
            "username and password")
        return -1

    atexit.register(Disconnect, si)

    content = si.RetrieveContent()

    snapshots_deleted = 0
    snapshots_created = 0

    for child in content.rootFolder.childEntity:
        if hasattr(child, 'vmFolder'):
            datacenter = child
            vmFolder = datacenter.vmFolder
            vmList = vmFolder.childEntity
            for vm in vmList:
                snapshot_name = date.today().isoformat()
                summary = vm.summary
                if summary.config.name != 'docker':
                    continue
                logger.info("Name       : %s" % summary.config.name)
                logger.debug("Path       : %s" % summary.config.vmPathName)
                logger.debug("Guest      : %s" % summary.config.guestFullName)
                annotation = summary.config.annotation
                if annotation != None and annotation != "":
                    logger.debug("Annotation : %s" % annotation)
                logger.debug("State      : %s" % summary.runtime.powerState)
                if summary.guest != None:
                    ip = summary.guest.ipAddress
                    if ip != None and ip != "":
                        logger.debug("IP         : %s" % ip)
                if summary.runtime.question != None:
                    logger.debug("Question  : %s" % summary.runtime.question.text)
                if summary.guest.toolsRunningStatus == 'guestToolsNotRunning':
                    logger.debug("tools not running")
                # print(summary)

                if vm.snapshot is not None:
                    snapshot_paths = list_snapshots_recursively(vm.snapshot.rootSnapshotList)
                    for snapshot in snapshot_paths:
                        logger.debug("Name: %s; Description: %s; CreateTime: %s; State: %s" % (
                            snapshot['name'],
                            snapshot['description'],
                            snapshot['createTime'],
                            snapshot['state']
                        ))
                        if snapshot_name == snapshot['name']:
                            snapshot_name = "%s" % (datetime.now().isoformat(timespec='seconds'))
                    snapshots_no = len(snapshot_paths)
                else:
                    snapshots_no = 0

                if snapshots_no < args.keep:
                    logger.info("%i snapshots found, should create a snapshot" % (snapshots_no))
                    create_snapshot(vm, snapshot_name)
                    snapshots_created += 1
                elif snapshots_no == args.keep:
                    logger.info("%i snapshots found, should create a snapshot and delete oldest one" % (snapshots_no))
                    logger.debug("oldest snapshot name: '%s'" % snapshot_paths[0]['name'])
                    create_snapshot(vm, snapshot_name)
                    snapshots_created += 1
                    delete_snapshot_by_name(vm.snapshot.rootSnapshotList, snapshot_paths[0]['name'])
                    snapshots_deleted += 1
                else:
                    logger.info("%i snapshots found, should create a snapshot and delete all but %i" % (snapshots_no, (args.keep-1)))
                    create_snapshot(vm, snapshot_name)
                    snapshots_created += 1
                    to_delete = snapshots_no - (args.keep-1)
                    delete_count = 0
                    for snapshot in snapshot_paths:
                        logger.debug("Deleting snapshot '%s' %s" % (snapshot['name'], snapshot['createTime']))
                        delete_snapshot_by_name(vm.snapshot.rootSnapshotList, snapshot['name'])
                        snapshots_deleted += 1
                        delete_count += 1
                        if delete_count >= to_delete:
                            logger.debug("deleted %i snapshots, %i left of %i total" % (delete_count, (args.keep-1), snapshots_no))
                            break
    print("done rotating snapshots, %i created, %i deleted" % (snapshots_created, snapshots_deleted))
    return 0

def create_snapshot(vm, snapshot_name):

    logger.debug("creating snapshot of VM '%s' using name '%s'" % (vm.summary.config.name, snapshot_name))
    WaitForTask(vm.CreateSnapshot_Task(
        name=snapshot_name,
        memory=False,
        quiesce=False,
        description='Automatic snapshot taken by snapshot rotator tool'
    ))

def list_snapshots_recursively(snapshots):
    snapshot_data = []
    snap_text = ""
    for snapshot in snapshots:
        snap_text = "Name: %s; Description: %s; CreateTime: %s; State: %s" % (
            snapshot.name,
            snapshot.description,
            snapshot.createTime,
            snapshot.state
        )
        # snapshot_data.append(snap_text)
        snapshot_data.append(dict(name=snapshot.name, description=snapshot.description, createTime=snapshot.createTime, state=snapshot.state))
        snapshot_data = snapshot_data + list_snapshots_recursively(snapshot.childSnapshotList)
    return snapshot_data

def delete_snapshot_by_name(snapshots, snapname):
    logger.debug("deleting snapshot '%s'" % snapname)
    snap_obj = []
    for snapshot in snapshots:
        if snapshot.name == snapname:
            snap_obj.append(snapshot)
        else:
            snap_obj = snap_obj + get_snapshots_by_name_recursively(snapshot.childSnapshotList, snapname)
    WaitForTask(snap_obj[0].snapshot.RemoveSnapshot_Task(False))

# Start program
if __name__ == "__main__":
   main()
