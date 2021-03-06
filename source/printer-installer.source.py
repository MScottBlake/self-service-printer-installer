#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
self-service-printer-installer

See the wiki! https://github.com/haircut/self-service-printer-installer/wiki
"""

import sys
import syslog
import os
import subprocess
import json
import argparse

__version__ = "0.2.0"

BRANDICON = "{config[gui][brand_icon]}" # pylint: disable=line-too-long
PRINTERICON = "{config[gui][printer_icon]}" # pylint: disable=line-too-long

# Path to Jamf binary
JAMF = "/usr/local/bin/jamf"
CDPATH = "{config[cocoaDialog][path]}" # pylint: disable=line-too-long


###############################################################################
# Queue Definitions
###############################################################################

JSON_DEFINITIONS = \
"""
{queues}
"""

QUEUE_DEFINITIONS = json.loads(JSON_DEFINITIONS)

###############################################################################
# Program Logic - Here be dragons!
###############################################################################


class Logger(object):
    """Super simple logging class"""
    @classmethod
    def log(cls, message, log_level=syslog.LOG_ALERT):
        """Log to the syslog and stdout"""
        syslog.syslog(log_level, "PRINTMAPPER: " + message)
        print message


# Initialize Logger
Logger()


def parse_args():
    """Set up argument parser"""
    parser = argparse.ArgumentParser(
        description=("Maps or 'installs' a printer queue after displaying "
                     "a list of available printer queues to the user. "
                     "Can specify a preselected_queue as argument 4, a filter "
                     "key as argument 5, and a filter value as arugment 6.")
    )
    parser.add_argument("jamf_mount", type=str, nargs='?',
                        help="Jamf-passed target drive mount point")
    parser.add_argument("jamf_hostname", type=str, nargs='?',
                        help="Jamf-passed computer hostname")
    parser.add_argument("jamf_user", type=str, nargs='?',
                        help="Jamf-passed name of user running policy")
    parser.add_argument("preselected_queue", type=str, nargs='?',
                        help="DisplayName of an available queue to map "
                             "without prompting user for selection")
    parser.add_argument("filter_key", type=str, nargs='?',
                        help="Field name of an attribute which you would "
                             "like to filter the available queues base upon")
    parser.add_argument("filter_value", type=str, nargs='?',
                        help="Value to search the provided filter_key "
                             "attribute for")

    return parser


def show_message(message_text, heading="{config[gui][window_title]}"):
    """Displays a message to the user via cocoaDialog"""
    showit = subprocess.Popen([CDPATH,
                               'ok-msgbox',
                               '--title', "{config[gui][window_title]}",
                               '--text', heading,
                               '--informative-text', message_text,
                               '--icon-file', BRANDICON,
                               '--float', '--no-cancel'])
    message_return, error = showit.communicate()
    return True


# pylint: disable=C0103
def error_and_exit(no_cocoaDialog=False):
    """
    Display a generic error message (if cocoaDialog is installed) then quit
    the program.
    """
    if not no_cocoaDialog:
        show_message("{config[gui][messages][error_undefined]}", "Error") # pylint: disable=line-too-long
    Logger.log("An error occurred which requires exiting this program.")
    sys.exit(1)


def run_jamf_policy(trigger, quiet=False):
    """Runs a jamf policy given the provided trigger"""
    if not quiet:
        progress_bar = subprocess.Popen([CDPATH, 'progressbar',
                                         '--title', 'Please wait...',
                                         '--text', 'Installing software...',
                                         '--float', '--indeterminate'],
                                        stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE)

    jamf_policy = subprocess.Popen([JAMF, 'policy', '-event', trigger],
                                   stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE)

    policy_return, error = jamf_policy.communicate()

    if not quiet:
        progress_bar.terminate()

    if "No policies were found for the" in policy_return:
        Logger.log("Unable to run Jamf policy via trigger " + trigger)
        return False
    elif "Submitting log to" in policy_return:
        Logger.log("Successfully ran Jamf policy via trigger " + trigger)
        return True

    return False

def check_for_cocoadialog():
    """
    Checks for the existence of cocoaDialog at the specified path. If it's not
    there, install it via the specified policy trigger.
    """
    if not os.path.exists(CDPATH):
        return run_jamf_policy("{config[cocoaDialog][install_trigger]}", True)

    return True


def get_currently_mapped_queues():
    """Return a list of print queues currently mapped on the system"""
    try:
        Logger.log('Gathering list of currently mappped queues')
        lpstat_result = subprocess.check_output(['/usr/bin/lpstat', '-p'])
    except subprocess.CalledProcessError:
        Logger.log('No current print queues found')
        lpstat_result = None

    current_queues = []
    if lpstat_result:
        for line in lpstat_result.splitlines():
            current_queues.append(line.split()[1])

    return current_queues


def build_printer_queue_list(current_queues, filter_key, filter_value, user_groups):
    """Builds a list of available print queues for GUI presentation"""
    display_list = []
    for queue in QUEUE_DEFINITIONS.values():

        # Skip if the printer is already installed
        if queue.get('DisplayName') in current_queues:
            continue

        # Skip if the CUPSName field is present and is already installed
        if queue.get('CUPSName') in current_queues:
            continue

        # Skip if a filter is enabled and it doesn't match
        if filter_key and queue.get(filter_key):
            if filter_value not in queue.get(filter_key):
                continue

        # Skip if a filter group is configured for this printer and the user is
        # not a member of the group
        if queue.get('ADFilterGroup'):
            if queue.get('ADFilterGroup') not in user_groups:
                continue

        # Add the printer to the list of available printers
        display_list.append(queue.get('DisplayName'))

    if not display_list:
        Logger.log("No currently-unmapped queues are available")
        show_message("{config[gui][messages][error_no_queues_available]}") # pylint: disable=line-too-long
        quit()

    return sorted(display_list)


def has_kerberos_ticket():
    """ Returns a boolean value indicating whether a Kerberos ticket exists. """
    return not subprocess.call(['klist', '-s'])


def user_ldap_groups(username):
    """ Returns a list of the groups that the user is a member of.
        Returns False if it can't find the username or throws an exception.
        It's up to the caller to ensure that the username they're using exists!
    """
    Logger.log("Generating list of user's AD groups")
    user_groups = []

    if not has_kerberos_ticket():
        Logger.log("Kerberos ticket not found. AD group filtering disabled.")
        return user_groups

    try:
        dn = subprocess.check_output(['ldapsearch', '-LLL',
                                      '-o', 'ldif-wrap=no',
                                      '-H', '{config[ldap][server]}',
                                      '-b', '{config[ldap][search_base]}',
                                      '(&(objectCategory=Person)(objectClass=User)(sAMAccountName='+username+'))', # pylint: disable=line-too-long
                                      'dn']
                                    ).splitlines()
    except subprocess.CalledProcessError as e:
        if e.returncode == 254:
            Logger.log('Encountered an authentication error while searching ldap.')
        else:
            Logger.log('Unknown error searching ldap. (Error code: '+str(e.returncode)+')')

        show_message("{config[ldap][messages][error]}") # pylint: disable=line-too-long
        quit()

    user_dn = ''
    for attribute in dn:
        if attribute.startswith('dn: '):
            user_dn = attribute.split(':')[1].strip()
            break

    search_filter = '(member:1.2.840.113556.1.4.1941:='+user_dn+')'
    if "{config[ldap][group][name_format]}" != "":
        search_filter = '(&'+search_filter+'(cn={config[ldap][group][name_format]}))'

    groups = subprocess.check_output(['ldapsearch', '-LLL',
                                      '-o', 'ldif-wrap=no',
                                      '-H', '{config[ldap][server]}',
                                      '-b', '{config[ldap][search_base]}',
                                      search_filter,
                                      'cn']
                                    ).splitlines()

    for attribute in groups:
        if attribute.startswith('cn: '):
            user_groups.append(attribute.split(':')[1].strip())

    return user_groups


def prompt_queue(list_of_queues):
    """Prompts the user to select a queue name"""
    Logger.log('Prompting user to select desired queue')
    queue_dialog = subprocess.Popen([CDPATH, 'dropdown', '--string-output',
                                     '--float', '--icon', 'gear',
                                     '--title', 'Select Print Queue',
                                     '--text', ('Choose a print queue to '
                                                'add to your computer:'),
                                     '--button1', 'Add',
                                     '--button2', 'Cancel',
                                     '--items'] + list_of_queues,
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE)
    prompt_return, error = queue_dialog.communicate()
    if prompt_return != "Cancel\n":
        selected_queue = prompt_return.splitlines()[1]
        Logger.log('User selected queue ' + selected_queue)
        return selected_queue

    Logger.log('User canceled queue selection')
    return False


def install_drivers(trigger):
    """Installs required drivers via Jamf policy given a trigger value"""
    Logger.log("Attempting to install drivers via policy trigger " + trigger)

    if not run_jamf_policy(trigger):
        return False

    return True


def search_for_driver(driver, trigger):
    """Searches the system for the appropriate driver and if not found,
       attempts to install it via Jamf policy"""
    if not os.path.exists(driver):
        Logger.log("The driver was not found at " + driver)
        if not install_drivers(trigger):
            show_message("{config[gui][messages][error_driver_failure]}") # pylint: disable=line-too-long
            Logger.log('Quitting program')
            quit()


def add_queue(queue):
    """Add the printer queue to the computer"""
    # Reference the queue dictionary by name
    q = QUEUE_DEFINITIONS[queue]

    # Determine whether we need to handle custom drivers
    # By convention, a driver path only appears in the queue dict if a custom
    # driver is required. Queues using the generic postscript driver have this
    # dict attribute set to "None" so we can test for truth
    if q['Driver']:
        Logger.log("Queue " + q['DisplayName'] + " requires a vendor driver")
        search_for_driver(q['Driver'], q['DriverTrigger'])
        q_driver = q['Driver']
    else:
        Logger.log(q['DisplayName'] + " uses a generic driver")
        # Specify the path to the default postscript drivers
        q_driver = "{config[default_driver]}" # pylint: disable=line-too-long

    # Common command
    cmd = ['/usr/sbin/lpadmin',
           '-p', q['DisplayName'],
           '-L', q['Location'],
           '-E',
           '-v', q['URI'],
           '-P', q_driver]

    # Determine Options
    if q['Options']:
        options = []
        for key, val in q['Options'].iteritems():
            options.append('-o')
            options.append(key + '=' + val)
        cmd = cmd + options

    mapq = subprocess.Popen(cmd,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            shell=False)
    try:
        map_return, error = mapq.communicate()
        Logger.log("Excuting command: " + ' '.join(cmd))
        Logger.log("Queue " + q['DisplayName'] + " successfully mapped")
        show_message(("{config[gui][messages][success_queue_added]}" # pylint: disable=line-too-long
                      % q['DisplayName']), "Success!")
        quit()
    except subprocess.CalledProcessError:
        Logger.log('There was a problem mapping the queue!')
        Logger.log('Attempted command: ' + ' '.join(cmd))
        show_message("{config[gui][messages][error_unable_map_queue]}") # pylint: disable=line-too-long
        quit()


def main():
    """Manage arguments and run workflow"""
    # Parse command line / Jamf-passed arguments
    parser = parse_args()
    # parse_known_args() works around potentially empty arguments passed by
    # a Jamf policy
    args = parser.parse_known_args()[0]

    # Build list of user's AD groups
    user_groups = user_ldap_groups(args.jamf_user)

    # Build list of currently mapped queues on client
    currently_mapped_queues = get_currently_mapped_queues()
    # Build list of available queues excluding currently-mapped queues
    available_queues = build_printer_queue_list(currently_mapped_queues,
                                                args.filter_key,
                                                args.filter_value,
                                                user_groups)

    # Determine if a pre-selected print queue was passed

    if args.preselected_queue:
        # Ensure pre-selected queue is actually available
        if args.preselected_queue in available_queues:
            selected_queue = args.preselected_queue
        else:
            show_message(("{config[gui][messages][error_preselected_queue]}")
                         % args.preselected_queue)
            error_and_exit()
    else:
        # Make sure cocoaDialog is installed
        if not check_for_cocoadialog():
            error_and_exit()

        # Prompt for a queue selection
        selected_queue = prompt_queue(available_queues)

    # Map the queue
    if selected_queue:
        add_queue(selected_queue)


if __name__ == '__main__':
    main()
