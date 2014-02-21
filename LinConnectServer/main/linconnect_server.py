'''
    LinConnect: Mirror Android notifications on Linux Desktop

    Copyright (C) 2013  Will Hauck

    This program is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with this program.  If not, see <http://www.gnu.org/licenses/>.
'''

from __future__ import print_function

# Imports
try:
    import ConfigParser
except ImportError:
    import configparser as ConfigParser
import os
import sys
import select
import threading
import platform
import time
from PIL import Image

import sphero
import cherrypy
import subprocess
from gi.repository import Notify
import pybonjour
import shutil

app_name = 'linconnect-server'
version = "3"

# Global Variables
_notification_header = ""
_notification_description = ""
s = sphero.Sphero(raw_input('What port is your sphero on? '))

# Configuration
script_dir = os.path.abspath(os.path.dirname(__file__))
s.connect()

def fade_to(s, r, g, b, up=False, loop=100):
    if not up:
        colors = [r, g, b]
        for i in range(loop):
            colors[0] -= r / loop
            colors[1] -= g / loop
            colors[2] -= b / loop
            s.set_rgb(*colors)
            time.sleep(0.03)
    else:
        colors = [0, 0, 0]
        for i in range(loop):
            colors[0] += r / loop
            colors[1] += g / loop
            colors[2] += b / loop
            s.set_rgb(*colors)
            time.sleep(0.03)

def cycle(s, r, g, b, loops=100, j=2):
    for i in range(j):
        fade_to(s, r, g, b, up=True, loop=loops)
        fade_to(s, r, g, b, up=False, loop=loops)
    s.set_rgb(0, 0, 0)

def user_specific_location(type, file):
    dir = os.path.expanduser(os.path.join('~/.' + type, app_name))
    if not os.path.isdir(dir):
        os.makedirs(dir)
    return os.path.join(dir, file)

conf_file = user_specific_location('config', 'conf.ini')
icon_path = user_specific_location('cache', 'icon_cache.png')

old_conf_file = os.path.join(script_dir, 'conf.ini')
if os.path.isfile(old_conf_file):
    if os.path.isfile(conf_file):
        print("Both old and new config files exist: %s and %s, ignoring old one" % (old_conf_file, conf_file))
    else:
        print("Old config file %s found, moving to a new location: %s" % (old_conf_file, conf_file))
        shutil.move(old_conf_file, conf_file)
del old_conf_file

try:
    with open(conf_file):
        print("Loading conf.ini")
except IOError:
    print("Creating conf.ini")
    with open(conf_file, 'w') as text_file:
        text_file.write("""[connection]
port = 9090
enable_bonjour = 1

[other]
enable_instruction_webpage = 1
notify_timeout = 5000""")

parser = ConfigParser.ConfigParser()
parser.read(conf_file)
del conf_file

# Must append port because Java Bonjour library can't determine it
_service_name = platform.node()

from PIL import Image

class PixelCounter(object):
  ''' loop through each pixel and average rgb '''
  def __init__(self, imageName):
      self.pic = Image.open(imageName)
      # load image data
      self.imgData = self.pic.load()
  def averagePixels(self):
      r, g, b = 0, 0, 0
      count = 0
      for x in xrange(self.pic.size[0]):
          for y in xrange(self.pic.size[1]):
            clrs = self.imgData[x,y]
            r += clrs[0]
            g += clrs[1]
            b += clrs[2]
            count += 1
      # calculate averages
      return (r/count), (g/count), (b/count), count

class Notification(object):
    if parser.getboolean('other', 'enable_instruction_webpage') == 1:
        with open(os.path.join(script_dir, 'index.html'), 'rb') as f:
            _index_source = f.read()

        def index(self):
            return self._index_source % (version, "<br/>".join(get_local_ip()))

        index.exposed = True

    def notif(self, notificon):
        global _notification_header
        global _notification_description

        # Get icon
        try:
            os.remove(icon_path)
        except:
            print("Creating icon cache...")
        file_object = open(icon_path, "a")
        while True:
            data = notificon.file.read(8192)
            if not data:
                break
            file_object.write(str(data))
        file_object.close()

        # Ensure the notification is not a duplicate
        if (_notification_header != cherrypy.request.headers['NOTIFHEADER']) \
        or (_notification_description != cherrypy.request.headers['NOTIFDESCRIPTION']):

            # Get notification data from HTTP header
            _notification_header = cherrypy.request.headers['NOTIFHEADER'].replace('\x00', '').decode('iso-8859-1', 'replace').encode('utf-8')
            _notification_description = cherrypy.request.headers['NOTIFDESCRIPTION'].replace('\x00', '').decode('iso-8859-1', 'replace').encode('utf-8')

            # Send the notification
            notif = Notify.Notification.new(_notification_header, _notification_description, icon_path)
            if parser.has_option('other', 'notify_timeout'):
                notif.set_timeout(parser.getint('other', 'notify_timeout'))
            try:
                notif.show()
                pc = PixelCounter(icon_path)
                cycle(s, *pc.averagePixels()[:3], loops=15, j=4)
            except:
                # Workaround for org.freedesktop.DBus.Error.ServiceUnknown
                Notify.uninit()
                Notify.init("com.willhauck.linconnect")
                notif.show()
                pc = PixelCounter(icon_path)
                cycle(s, *pc.averagePixels()[:3], loops=15, j=4)

        return "true"
    notif.exposed = True


def register_callback(sdRef, flags, errorCode, name, regtype, domain):
    if errorCode == pybonjour.kDNSServiceErr_NoError:
        print("Registered Bonjour service " + name)


def initialize_bonjour():
    sdRef = pybonjour.DNSServiceRegister(name=_service_name,
                                     regtype="_linconnect._tcp",
                                     port=int(parser.get('connection', 'port')),
                                     callBack=register_callback)
    try:
        try:
            while True:
                ready = select.select([sdRef], [], [])
                if sdRef in ready[0]:
                    pybonjour.DNSServiceProcessResult(sdRef)
        except KeyboardInterrupt:
            pass
    finally:
        sdRef.close()


def get_local_ip():
    ips = []
    for ip in subprocess.check_output("/sbin/ip address | grep -i 'inet ' | awk {'print $2'} | sed -e 's/\/[^\/]*$//'", shell=True).split("\n"):
        if ip.__len__() > 0 and not ip.startswith("127."):
            ips.append(ip + ":" + parser.get('connection', 'port'))
    return ips

# Initialization
if not Notify.init("com.willhauck.linconnect"):
    raise ImportError("Error initializing libnotify")

# Start Bonjour if desired
if parser.getboolean('connection', 'enable_bonjour') == 1:
    thr = threading.Thread(target=initialize_bonjour)
    thr.start()

config_instructions = "Configuration instructions at http://localhost:" + parser.get('connection', 'port')
print(config_instructions)
notif = Notify.Notification.new("Notification server started", config_instructions, "info")
notif.show()

cherrypy.server.socket_host = '0.0.0.0'
cherrypy.server.socket_port = int(parser.get('connection', 'port'))

cherrypy.quickstart(Notification())
