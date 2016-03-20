# TPPBR MusicCat Song Library
# Dependencies: pyyaml, python-Levenshtein, pypiwin32 (windows-only)
# see also setup.py, python-Levenshtein needs compilation or manual installation via binary.
# Please install all with pip3

# (note: if installing python-Levenshtein complains about vcvarsall.bat,
#  see http://stackoverflow.com/a/33163704)

from __future__ import print_function
try:
    from builtins import input
except: # Temporary hack until the builtins future module is properly installed
    input = raw_input

# pip3 dependencies
import Levenshtein
import yaml

# standard modules
import os
import subprocess
import logging
from collections import namedtuple

import winamp

class NoMatchError(ValueError):
    """Raised when a song id fails to match a song with any confidence"""
    def __init__(self, song_id):
        super().__init__("Song ID {} not found.".format(song_id))
        self.song_id = song_id
        
class SongIdConflictError(ValueError):
    """Raised when a song id occurs twice."""
    def __init__(self, song_id):
        super().__init__("Song ID {} already in use.".format(song_id))
        self.song_id = song_id

Song = namedtuple("Song", ("id", "title", "path", "types", "game", "fullpath"))
Game = namedtuple("Game", ("id", "title", "platform", "year", "series", "path"))

class MusicCat(object):

    def __init__(self, library_path, winamp_path, disable_nobrstm_exception=False, disable_auto_load=False):
        self.library_path = library_path
        self.winamp_path = winamp_path
        self.disable_nobrstm_exception = disable_nobrstm_exception
        self.songs = {}
        self.winamp = winamp.Winamp()
        self.log = logging.getLogger("musicCat")
        self.paused = False

        if not disable_auto_load:
            self.refresh_song_list()

    def refresh_song_list(self):
        """Clears songlist and loads all metadata.yaml files under self.library_path"""
        self.songs = {}
        for root, dirs, files in os.walk(self.library_path):
            for filename in files:
                if filename.endswith(".yaml"):
                    metafilename = os.path.join(root, filename)
                    try:
                        self._import_metadata(metafilename)
                    except Exception as e:
                        self.log.error("Exception while loading file {}: {}".format(metafilename, e))
        if len(self.songs) == 0:
            self.log.warn("No metadata found! MusicCat isn't going to do very much. (Current music library location: {} )".format(self.library_path))
    """
    Metadata.yaml format:

     - id: gameid
       title:
       series:
       year:
       platform:
       path: # No longer used
       songs:
        - id:
          title:
          path:
          type: type
          types: [type, type] #one or the other, depending on multiple
    """

    def _import_metadata(self, metafilename):
        """Import metadata given a metadata filename. Assumed to be one game per metadata file."""
        with open(metafilename) as metafile:
            gamedata = yaml.load(metafile)
        path = os.path.dirname(metafilename)
        newsongs = {}

        songs = gamedata.pop("songs")
        if 'series' not in gamedata:
            gamedata['series'] = None
        game = Game(**gamedata)

        for song in songs:
            song["fullpath"] = os.path.join(path, song["path"])
            song["game"] = game

            # Convert single type to a stored list
            if "type" in song:
                song["types"] = [song.pop("type")]
            
            newsong = Song(**song)

            #some sanity checks
            if newsong.id in self.songs:
                self.log.critical("Songid conflict! {} exists twice, once in {} and once in {}!".format(newsong.id, self.songs[newsong.id].game.id, game.id))
                raise SongIdConflictError(newsong.id)
            if newsong.id in newsongs:
                self.log.critical("Songid conflict! {} exists twice in the same game, {}.".format(newsong.id, game.id))
                raise SongIdConflictError(newsong.id)
            if not os.path.isfile(newsong.fullpath):
                self.log.error("Songid {} doesn't have a BRSTM file at {}!".format(newsong.id, newsong.fullpath))
                if not self.disable_nobrstm_exception:
                    raise FileNotFoundError(newsong.fullpath)
            #add to song list!
            self.songs[newsong.id] = newsong

    def _play_file(self, songfile):
        """Plays the given song file. 
        Though this may appear to, using subprocess.Popen does not leak memory because winamp makes the processes all exit."""
        self.winamp.stop()
        self.winamp.clearPlaylist()
        p = subprocess.Popen('"{0}" "{1}"'.format(self.winamp_path, songfile))

    def search(self, keywords, cutoff=0.3):
        """Search through all songs in self.songs.
        Determines all songs being matched by the supplied keywords.
        Returns a list of tuples of the form (song, matchratio), where matchratio goes from <cutoff> to 1.0;
        1.0 being a perfect match. The result is sorted by that value, highest match ratios first."""

        num_keywords = len(keywords)
        results = []
        for song in self.songs.values():
            # search in title and gametitle
            haystack1, haystack2 = set(song.title.lower().split()), set(song.game.title.lower().split())
            ratio = 0
            for keyword in keywords:
                keyword = keyword.lower()
                # determine best keyword match
                subratio1 = max(Levenshtein.ratio(keyword, word) for word in haystack1)
                subratio2 = max(Levenshtein.ratio(keyword, word) for word in haystack2)
                subratio = max(subratio1,subratio2*0.8)
                if subratio < 0.7:
                    # assume low ratios are no match
                    subratio = 0
                ratio += subratio
            ratio /= num_keywords
            
            if ratio > cutoff:
                # random cutoff value
                results.append((song, ratio))
            
        return sorted(results, key=lambda s: s[1], reverse=True)

    def play_song(self, song_id):
        """Play a song. May raise a NoMatchError if the song_id doesn't exist."""
        if song_id not in self.songs:
            raise NoMatchError(song_id)
        nextsong = self.songs[song_id]
        self.current_song = nextsong
        self._play_file(nextsong.fullpath)
        self.log.info("Now playing {}".format(nextsong))

    def set_volume(self, volume):
        """Update the volume. Volume goes from 0.0 to 1.0"""
        if (volume < 0) or (volume > 1):
            raise ValueError("Volume must be between 0 and 1")
        #winamp expects a volume from 0 to 255
        self.winamp.setVolume(volume*255)

    def pause(self):
        """Pauses the current song. Unpauses if already paused"""
        self.winamp.pause()
        self.paused = True

    def unpause(self):
        """Unpauses the current song. Does nothing if it wasn't paused before."""
        #winamp.play() will restart the song from the beginning if not paused.
        #If you want to restart the song, just call play_song with the same song.
        if self.paused:
            self.winamp.play()
            self.paused = False

def rtfm():
    print("""Usage:
    musiccat.py count [category]     prints the total amount of songs found. filtered by a category if supplied
    musiccat.py play <song_id>       plays the song identified by the given song id
    musiccat.py pause                pauses the current song (resumes if already paused)
    musiccat.py unpause              resumes the current song (restarts the song if already running)
    musiccat.py volume <volume>      sets the volume, float between 0.0 and 1.0
    musiccat.py search <keyword>...  searches for a song by keywords and returns the best match""")

def main():
    #assumed windows-only for now
    import sys
    
    winamp_path = os.path.expandvars("%PROGRAMFILES(X86)%/Winamp/winamp.exe")
    musiccat = MusicCat(".", winamp_path, disable_nobrstm_exception=True)

    #command-line access
    #run "musiccat.py search <song_id> to call musiccat.search("song_id"), for example
    #or "musiccat.py amt_songs"
    if len(sys.argv) < 2:
        rtfm()
        return
    
    command = sys.argv[1]
    args = sys.argv[2:]
    if command == "count":
        category = None
        if args:
            category = args[0]
            count = sum(1 for song in musiccat.songs.values() if category in song.types)
            print("Number of songs in category %s: %d" % (category, count))
        else:
            print("Number of songs: %d" % len(musiccat.songs))
    elif command == "play" and args:
        try:
            musiccat.play_song(args[0])
        except NoMatchError:
            print("No song with that id")
    elif command == "pause":
        musiccat.pause()
    elif command == "unpause":
        musiccat.unpause()
    elif command == "volume" and args:
        try:
            volume = float(args[0])
            if not 0.0 <= volume <= 1.0:
                raise ValueError("Invalid volume range")
            musiccat.set_volume(volume)
        except ValueError:
            print("Volume must be a float between 0.0 and 1.0")
    elif command == "search" and args:
        songs = musiccat.search(args)
        if not songs:
            print("No songs found.")
        else:
            # maximum of 5 results
            limit = 5
            count = len(songs)
            best = songs[:limit]
            print("Found %d songs, best matches first:" % count)
            for song, score in best:
                print("%4.0f%%: %s (%s)" % (score*100, song.title, song.game.title))
            if count > limit:
                print("and %d more" % (count - limit))
    else:
        rtfm()

if __name__ == "__main__":
    main()
