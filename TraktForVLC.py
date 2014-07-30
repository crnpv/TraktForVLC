#!/usr/bin/env python
# encoding: utf-8
#
# TraktForVLC, to link VLC watching to trakt.tv updating
#
# Copyright (C) 2012        quietcore
# Copyright (C) 2013        Damien Battistella
# Copyright (C) 2014        Raphaël Beamonte
#
# This file is part of TraktForVLC.  TraktForVLC is free software: you can
# redistribute it and/or modify it under the terms of the GNU General Public
# License as published by the Free Software Foundation, version 2.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE.  See the GNU General Public License for more
# details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software Foundation,
# Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA
# or see <http://www.gnu.org/licenses/>.

#import urllib2
import logging
import ConfigParser
from vlcrc import VLCRemote
import movie_info
import TraktClient
import sys
import time
import re
import os
import getopt
import datetime
import traceback
from copy import deepcopy
from tvrage import api as tvrage_api
from tvrage import feeds as tvrage_feeds

VERSION = "0.3"
VLC_VERSION = VLC_DATE  = ""
TIMER_INTERVAL = START_WATCHING_TIMER = 0

# Current date
DATETIME = datetime.datetime.now()

# In DEBUG level, timers're forced to 5secs
#LOG_LEVEL = logging.DEBUG
LOG_LEVEL = logging.INFO
#LOG_LEVEL = logging.WARNING
#LOG_LEVEL = logging.ERROR

class TraktForVLC(object):

    def __init__(self, datadir, configfile):

        # Process log file name
        if LOG_LEVEL is logging.DEBUG:
            logfile = datadir + "/logs/TraktForVLC-DEBUG.log"
            # Remove existing DEBUG file
            if os.path.isfile(logfile):
                os.remove(logfile)
        else:
            logfile = datadir + "/logs/TraktForVLC-" + DATETIME.strftime("%y-%m-%d-%H-%M") + ".log"

        logging.basicConfig(format="%(asctime)s::%(name)s::%(levelname)s::%(message)s",
                            level=LOG_LEVEL,
                            filename=logfile,
                            stream=sys.stdout)

        self.log = logging.getLogger("TraktForVLC")
        self.log.info("----------------------------------------------------------------------------")
        self.log.info("                        TraktForVLC v" + VERSION + " by XaF")
        self.log.info("                           Last update : 07/28/2014")
        self.log.info("                        contact : raphael.beamonte@gmail.com")
        self.log.info("              Description : Allow scrobbling VLC content to Trakt")
        self.log.info("          Download : https://github.com/XaF/TraktForVLC.git")
        self.log.info("----------------------------------------------------------------------------")
        self.log.info("Initializing Trakt for VLC...")

        if not os.path.isfile(configfile):
            self.log.error("Config file " + configfile + " not found, exiting.")
            exit()

        self.config = ConfigParser.RawConfigParser()
        self.config.read(configfile)

        # Initialize timers
        if LOG_LEVEL is logging.DEBUG:
            self.TIMER_INTERVAL = 5
            self.START_WATCHING_TIMER = 5
            self.log.info("Logger level is set to DEBUG");
        else:
            self.TIMER_INTERVAL = int(self.config.get("TraktForVLC", "Timer"))
            self.START_WATCHING_TIMER = int(self.config.get("TraktForVLC", "StartWatching"))
            self.log.info("Logger level is set to INFO");

        self.log.info("-- Timer set to " + str(self.TIMER_INTERVAL) + " secs")
        self.log.info("-- Video will be marked as \"is watching\" from " + str(self.START_WATCHING_TIMER) + " secs")

        # VLC configuration
        self.vlc_ip = self.config.get("VLC", "IP")
        self.vlc_port = self.config.getint("VLC", "Port")

        self.log.info("Listening VLC to " + self.vlc_ip + ":" + str(self.vlc_port))

        # Trakt configuration
        trakt_api = "128ecd4886c86eabe4ef13675ad10495c916381a"
        trakt_username = self.config.get("Trakt", "Username")
        trakt_password = self.config.get("Trakt", "Password")

        self.log.info("Connect to Trakt(" + trakt_username + ", " + trakt_password + ")")

        # Initialize Trakt client
        self.trakt_client = TraktClient.TraktClient(trakt_api,
                                                    trakt_username,
                                                    trakt_password)

        self.resetCache()

        self.watching_now = ""
        self.timer = 0
        self.vlc_connected = True

    def resetCache(self, filename = None):
        self.log.debug("reset cache")
        self.cache = {
            "vlc_fileid": filename,
            "scrobbled": False,
            "movie_info": None,
            "series_info": None,
            "watching": -1,
        }

    def run(self):

        while (True):
            self.timer += self.TIMER_INTERVAL
            try:
                self.main()
            except Exception, e:
                self.log.error("An unknown error occurred : " + str(e))
                traceback.print_exc()
            time.sleep(self.TIMER_INTERVAL)
        self.main()

    def main(self):
        try:
            vlc = VLCRemote(self.vlc_ip, self.vlc_port)
            self.vlc_connected = True
        except:
            if self.vlc_connected:
                self.log.debug('Could not find VLC running at ' + str(self.vlc_ip) + ':'+ str(self.vlc_port))
                self.log.debug('Make sure your VLC player is running with --extraintf=rc --rc-host='+ str(self.vlc_ip) +':' + str(self.vlc_port) + ' --rc-quiet')
                self.vlc_connected = False
            return

        vlcStatus = vlc.is_playing()

        if not vlcStatus:
            vlc.close()
            return

        currentFile = "%s - %s" % (vlc.get_title("^(?!status change:)(.+?)\r?\n").group(1), vlc.get_length())
        if currentFile == self.cache["vlc_fileid"]:
            if self.cache["series_info"] is None and self.cache["movie_info"] is None:
                video = None
            elif self.cache["series_info"] is not None:
                video = self.get_TV(vlc, self.cache["series_info"])
            else:
                video = self.get_Movie(vlc, self.cache["movie_info"])
        else:
            self.resetCache(currentFile)

            video = self.get_TV(vlc)
            if video is None:
                video = self.get_Movie(vlc)

        if video is None:
            self.log.info("No tv show nor movie found for the current playing video")
            return

        logtitle = video["title"]
        if video["tv"]:
            logtitle += " - %01dx%02d" % (int(video["season"]), int(video["episode"]))

        self.log.debug("----------------------------------------------------------------------------");
        self.log.debug("                             Timer : " + str(self.timer))
        self.log.debug("----------------------------------------------------------------------------");
        self.log.info(logtitle + " state : " + str(video["percentage"]) + "%")
        self.log.debug("This video is scrobbled : " + str(self.cache["scrobbled"]))

        if video["percentage"] >= 90 and not self.cache["scrobbled"]:
            self.log.info("Scrobbling "+ logtitle + " to Trakt...")
            try:
                self.trakt_client.update_media_status(video["title"],
                                                        video["year"],
                                                        video["duration"],
                                                        video["percentage"],
                                                        VERSION,
                                                        VLC_VERSION,
                                                        VLC_DATE,
                                                        tv=video["tv"],
                                                        scrobble=True,
                                                        season=video["season"],
                                                        episode=video["episode"])
                self.cache["scrobbled"] = True
                self.log.info(logtitle + " scrobbled to Trakt !")
            except TraktClient.TraktError, (e):
                self.log.error("An error occurred while trying to scrobble: " + e.msg)
                if ("scrobbled" in e.msg and "already" in e.msg):
                    self.log.info("Seems we've already scrobbled this episode recently, aborting scrobble attempt.")
                    self.cache["scrobbled"] = True

        elif (video["percentage"] < 90
                and not self.cache["scrobbled"]
                and video["percentage"] != self.cache["watching"]
                and (float(video["duration"]) * float(video["percentage"]) / 100.0) >= self.START_WATCHING_TIMER):

            # self.timer = 0

            self.log.debug("Trying to mark " + logtitle + " watching on Trakt...")

            try:
                self.trakt_client.update_media_status(video["title"],
                                                        video["year"],
                                                        video["duration"],
                                                        video["percentage"],
                                                        VERSION,
                                                        VLC_VERSION,
                                                        VLC_DATE,
                                                        tv=video["tv"],
                                                        season=video["season"],
                                                        episode=video["episode"])

                self.log.info(logtitle + " is currently watching on Trakt...")
                self.cache["watching"] = video["percentage"]
            except TraktClient.TraktError, (e):
                self.timer = 870
                self.log.error("An error occurred while trying to mark watching " + logtitle + " : " + e.msg)

        vlc.close()

    def get_TV(self, vlc, series_info = (None, None, None)):
        try:
            series, seasonNumber, episodeNumber = series_info
            if series is None:
                now_playing = vlc.get_title("^(?!status change:)(?P<SeriesName>.+?)(?:[[(]?(?P<Year>[0-9]{4})[])]?.*)? *S?(?P<SeasonNumber>[0-9]+)(?:[ .XE]?(?P<EpisodeNumber>[0-9]{1,3})).*\.[a-z]{2,4}")
                seriesName = now_playing.group('SeriesName').rstrip(' -').replace('.', ' ')
                seriesYear = ifnull(now_playing.group('Year'),'1900')
                seasonNumber = ifnull(now_playing.group('SeasonNumber').lstrip('0'),'0')
                episodeNumber = ifnull(now_playing.group('EpisodeNumber').lstrip('0'),'0')

                if self.valid_TV(seriesName):
                    series = tvrage_api.Show(seriesName)
                    self.cache["series_info"] = (deepcopy(series), seasonNumber, episodeNumber)

            if series is not None:
                duration = int(vlc.get_length())
                time = int(vlc.get_time())
                percentage = time*100/duration
                try:
                    episode = series.season(int(seasonNumber)).episode(int(episodeNumber))
                    return self.set_video(True, episode.show, series.started, duration, percentage, episode.season, episode.number)
                except:
                    self.log.warning("Episode : No valid episode found !")
                    self.cache["series_info"] = None
                    return
        except:
            self.log.warning("No matching tv show found for video playing")
            return

    def valid_TV(self, seriesName):
        try:
            series = tvrage_feeds.full_search(seriesName)
            if (len(series) == 0):
                self.log.debug("Get_Title -> No Series found by file name.")
                return False
            return True
        except:
            self.log.debug("Valid_TV -> no valid title found.")
            return False

    def get_Movie(self, vlc, movie = None):
        try:
            if movie is None:
                now_playing = vlc.get_title("^(?!status change:)(?P<Title>.+?) ?(?:[[(]?(?P<Year>[0-9]{4})[])]?.*)? *\.[a-z]{2,4}")
                title = now_playing.group('Title')
                year = ifnull(now_playing.group('Year'), '')

                if self.valid_Movie(title, year, duration):
                    movie = self.cache["movie_info"]

            if movie is not None:
                duration = int(vlc.get_length())
                playtime = int(vlc.get_time())
                percentage = playtime*100/duration

                return self.set_video(False, movie['Title'], movie['Year'], duration, percentage)

            return
        except:
            self.log.debug("No matching movie found for video playing")
            return


    def valid_Movie(self, vlcTitle, vlcYear, vlcDuration):
        try:
            # Get Movie info
            movie = movie_info.get_movie_info(vlcTitle, vlcYear)
            # Compare Movie runtime against VLC runtime
            regex = re.compile('^((?P<hour>[0-9]{1,2}).*?h)?.*?(?P<min>[0-9]{1,2}).*?min?',re.IGNORECASE|re.MULTILINE)
            r = regex.search(movie['Runtime'])
            try:
                timeh = 0 if r.group('hour') is None else int(r.group('hour'))
                timem = 0 if r.group('min') is None else int(r.group('min'))
                time = timeh*60*60+timem*60
            except:
                return False
            # Verify that the VLC duration is within 5 minutes of the official duration
            if (vlcDuration >= time - 300) and (vlcDuration <= time + 300):
                self.cache["movie_info"] = deepcopy(movie)
                return True
        except:
            self.log.debug("Valid_Movie -> no valid title found")
            return False
        return False

    def set_video(self, tv, title, year, duration, percentage, season = -1, episode = -1):
        video = {}
        video["tv"] = tv
        video["title"] = title
        video["year"] = year
        video["duration"] = duration
        video["percentage"] = percentage
        video["season"] = season
        video["episode"] = episode
        return video

def ifnull(var, val):
    return val if var is None else var

def daemonize(pidfile=""):
    """
    Forks the process off to run as a daemon. Most of this code is from the
    sickbeard project.
    """

    if (pidfile):
        if os.path.exists(pidfile):
            sys.exit("The pidfile " + pidfile + " already exists, Trakt for VLC may still be running.")
        try:
            file(pidfile, 'w').write("pid\n")
        except IOError, e:
            sys.exit("Unable to write PID file: %s [%d]" % (e.strerror, e.errno))

    # Make a non-session-leader child process
    try:
        pid = os.fork() #@UndefinedVariable - only available in UNIX
        if pid != 0:
            sys.exit(0)
    except OSError, e:
        raise RuntimeError("1st fork failed: %s [%d]" % (e.strerror, e.errno))

    os.setsid() #@UndefinedVariable - only available in UNIX

    # Make sure I can read my own files and shut out others
    prev = os.umask(0)
    os.umask(prev and int('077', 8))

    # Make the child a session-leader by detaching from the terminal
    try:
        pid = os.fork() #@UndefinedVariable - only available in UNIX
        if pid != 0:
            sys.exit(0)
    except OSError, e:
        raise RuntimeError("2nd fork failed: %s [%d]" % (e.strerror, e.errno))

    dev_null = file('/dev/null', 'r')
    os.dup2(dev_null.fileno(), sys.stdin.fileno())

    if (pidfile):
        file(pidfile, "w").write("%s\n" % str(os.getpid()))

if __name__ == '__main__':
    should_pair = should_daemon = False
    pidfile = ""
    datadir = sys.path[0]
    logfile = ""
    config = ""

    try:
        opts, args = getopt.getopt(sys.argv[1:], "dp", ['daemon', 'pidfile=', 'datadir=', 'config=']) #@UnusedVariable
    except getopt.GetoptError:
        print "Available options: --daemon, --pidfile, --datadir, --config"
        sys.exit()

    for o, a in opts:
        # Run as a daemon
        if o in ('-d', '--daemon'):
            if sys.platform == 'win32':
                print "Daemonize not supported under Windows, starting normally"
            else:
                should_daemon = True

        # Create pid file
        if o in ('--pidfile',):
            pidfile = str(a)

        # Determine location of datadir
        if o in ('--datadir',):
            datadir = str(a)

        # Determine location of config file
        if o in ('--config',):
            config = str(a)

    if should_daemon:
        daemonize(pidfile)
    elif (pidfile):
        print "Pidilfe isn't useful when not running as a daemon, ignoring pidfile."

    if config == "":
        config = sys.path[0]
    configfile = config + "/config.ini"

    client = TraktForVLC(datadir, configfile)
    client.run()



