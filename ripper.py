#!/usr/bin/env python

from __future__ import division
import os
import sys
import argparse
import subprocess
import glob

parser = argparse.ArgumentParser()
parser.add_argument('input', help='Top level directory containing the DVD (e.g. /Volumes/foo/) ')
parser.add_argument('show', help='Name of the show')
parser.add_argument('series', help='Series of disk', type=int)
parser.add_argument('shorthand', help='Shorthand for series')
parser.add_argument('runtime', help='Approximate runtime in minutes', type=int)
parser.add_argument('--out_base_dir', help='Base level of output directory. Rips automatically organised by name and series', default='/Users/bs8959/Movies/TV')
parser.add_argument('-e', '--episodes', help='Number of episodes on the disk, if known', type=int)
parser.add_argument('-t', '--time-delta', help='Allowable time difference from approx_runtime', type=float, default=5)
parser.add_argument('-y', '--yes', help='Say yes to questions and don\'t ask for any confirmation', action='store_true', default=False)
args = parser.parse_args()

class NoAudioFoundError(Exception):
    pass

encode_params = ['-e', 'x264', '-q', '22.0', '-r', '30', '--pfr', '-E', 'faac', '-B', '128', '-6', 'dpl2', '-R', 'Auto', '-D', '0.0', '--audio-copy-mask', 'aac,ac3,dtshd,dts,mp3', '--audio-fallback', 'ffac3', '-f', 'mp4', '-Y', '382', '--loose-anamorphic', '--modulus', '2', '--x264-preset', 'medium', '--h264-profile', 'main', '--h264-level', '3.0', '-v', '0']

def run_handbrake(args):
    """
    Run handbrake with the supplied arguments. Returns the tuple (True/False, stderr) where stderr is the text returned by
    handbrake - handbrake only outputs to stderr.
    """
    p = subprocess.Popen(['HandBrakeCLI'] + args, stderr=subprocess.PIPE)
    (stdout, stderr) = p.communicate()

    success = p.returncode == 0
    return success, stderr

def get_episodes(handbrake_base_args, approx_runtime, allowable_time_delta, known_num_episodes = None):
    """
    Scan the disk and return a list of episodes. Each list element is a dict, with keys for 
    video title, audio title and duration. Best guesses are made.
    """
    handbrake_args = handbrake_base_args + ['-t', '0']

    success, stderr = run_handbrake(handbrake_args)
    if not success:
      print >> sys.stderr, 'Failed to read episode list. Error:', stderr
      return []

    all_episodes = _parse_episodes(stderr)
    selected_episodes = select_episodes(all_episodes, approx_runtime, allowable_time_delta, known_num_episodes)

    return selected_episodes

def select_episodes(episodes, approx_runtime, allowable_time_delta, known_num_episodes = None):
    """
    Identify the episodes that are likely to be correct based on the expected runtime and known
    information
    """
    selected = []
    for episode in episodes:
        time_delta = approx_runtime - abs(episode['duration'])
        if time_delta <= allowable_time_delta:
            selected.append(episode)

    if known_num_episodes is not None:
        if len(selected) != known_num_episodes:
            print >> sys.stderr, 'Number of episodes found is %d, expected: %d' % (len(selected), known_num_episodes)
            return []

    for episode in selected:
        if 'video_track' not in episode or 'audio_track' not in episode or 'audio_description' not in episode:
            print >> sys.stderr, 'Selected episode does not have the required information! Episode:', episode
            return [] 

    return selected

def _get_audio_track(audio_options):
    """
    Return the tuple (track number, track info) for the selected audio track. First track if there is only
    one track, otherwise, an attempt to extract the correct track will be made

    Note that it is only a problem if there is no audio if this title becomes an episode we attept to use
    """
    #Select audio. Default to first and only, handle other cases
    audio_index = 0
    if len(audio_options)  == 0:
        raise NoAudioFoundError
    elif len(audio_options) > 1:
        #Identify any containing 'eng'. If there is only one, pick that. Otherwise, abort
        eng_lang_tracks = [i for (i,t) in enumerate(audio_options) if t.lower().find('eng') != -1]
        if len(eng_lang_tracks) != 1:
            print >> sys.stderr, 'Unable to identify the correct audio track. Track listing:', audio_options
            raise NoAudioFoundError

        audio_index = eng_lang_tracks[0]

    audio_description = audio_options[audio_index]
    audio_track = int(audio_description.split()[1][:-1]) # Example line: + 2, English (AC3) (Dolby Surround)

    return (audio_track, audio_description)

def _parse_episodes(handbrake_response):
    """
    Extract the list of titles from the ugly output from HandBrake
    """
    episodes = []
    lines = handbrake_response.split('\n')
    extract_audo_level = None
    audio_options = []
    for line in lines:
        if len(line) == 0:
            continue

        #Handbrake output is prefixed with a number of spaces which delimit subsections
        #Use this to extract the different audio tracks so we can pick from them
        line_level = 0
        if line[0] == ' ':
            line_level = min([i for (i,c) in enumerate(line) if c != ' '])
        line = line.strip()
        if extract_audo_level is not None:
            if line_level > extract_audo_level:
                audio_options.append(line)
            else:
                try:
                    (track, description) = _get_audio_track(audio_options)
                    episodes[-1]['audio_track'] = track
                    episodes[-1]['audio_description'] = description
                except NoAudioFoundError:
                    pass
                extract_audo_level = None
                audio_options = []
        elif line.startswith('+ title'):
            video_track = line.split()[2][:-1] #Example line is '+ title 3:'
            video_track = int(video_track)
            episodes.append({'video_track': video_track})
        elif line.startswith('+ duration'):
            if 'duration' in episodes[-1]:
                print 'Title %d with video track %d already has a duration, cannot parse the episode list!' % (len(episodes), episodes[-1]['duration'])
                return []
            duration_string = line.split()[2] # Example line is '+ duration: 00:26:06'
            episodes[-1]['duration'] = get_duration_in_seconds(line.split()[2])
        elif line.startswith('+ audio tracks'):
            if 'audio_track' in episodes[-1]:
                print 'Title %d with video track %d already has a audio track, cannot parse the episode list!' % (len(episodes), episodes[-1]['audio_track'])
                return []
            extract_audo_level = line_level

    return episodes

def _get_episode_offset(series_dir, series, shorthand):
    """
    Glob the supplied directory for files of the correct format, finding 
    the largest current episode number
    """

    glob_pattern = '%s_S%d*_E[0-9]*.*' % (shorthand, series) 
    files = glob.glob(series_dir + '/' + glob_pattern)
    max_episode = 0
    for filename in files:
        basename_no_ext = '.'.join(os.path.basename(filename).split('.')[:-1])
        episode = basename_no_ext[basename_no_ext.rfind('E')+1:]
        max_episode = max(max_episode, int(episode))

    return max_episode

def check_environment(basedir, show, series, shorthand):
    """
    Check the output environment, making directories as required
    Return a tuple of (output dir, current episode offset)
    """
    episode_offset = 0

    if not os.path.isdir(basedir):
        print >> sys.stderr, "Base directory for TV rips doesn't exist!"
        sys.exit(1)

    show_dir = os.path.join(basedir, show)

    if not os.path.isdir(show_dir):
        print 'Making directory for this show:', show_dir
        os.mkdir(show_dir)

    series_dir_name = '%s S%d' % (shorthand, series)
    series_dir = os.path.join(show_dir, series_dir_name)

    if not os.path.isdir(series_dir):
        print 'Making directory for this series:', series_dir
        os.mkdir(series_dir)
    else:
        episode_offset = _get_episode_offset(series_dir, series, shorthand)

    return (series_dir, episode_offset)

def get_duration_in_seconds(duration_string):
    """
    Convert a time in the form of hh:mm:ss to a number of seconds
    """
    (hours, minutes, seconds) = map(float, duration_string.split(':'))
    return hours * 3600 + minutes * 60 + seconds

def get_length(filename):
    """
    Get the length of the supplied video in seconds file by calling ffprobe
    (Courtesty of http://stackoverflow.com/questions/3844430/how-to-get-video-duration-in-python-or-django/3844467#3844467)
    Returns -1 on error
    """
    p = subprocess.Popen(['ffprobe', filename], stderr=subprocess.PIPE)
    (stdout, stderr) = p.communicate()

    if p.returncode != 0:
        print >> sys.stderr, 'Error occurred calling ffprobe with:', filename
        return -1

    for line in stderr.split('\n'):
        if 'Duration' in line:
            #Example line: Duration: 00:28:45.01, start: 0.000000, bitrate: 1032 kb/s
            duration_string = line.split()[1][:-1]
            return get_duration_in_seconds(duration_string)

    print >> sys.stderr, 'No duration found in ffprobe output for:', filename
    return -1

if __name__ == "__main__":
    #Check environment and determine episode offset and final output directory
    output_dir, episode_offset = check_environment(args.out_base_dir, args.show, args.series, args.shorthand)

    print 'Output directory will be:', output_dir
    print 'Episode offset:', episode_offset

    print 'Getting episodes'
    episodes = get_episodes(['-i', args.input], args.runtime, args.time_delta * 60, args.episodes)

    if len(episodes) == 0:
        print >> sys.stderr, 'No episodes for encode found.'
        sys.exit(1)

    print 'Job List'
    print '-' * 40
    for index, episode in enumerate(episodes):
        output_name = '%s_S%d_E%d.m4v' % (args.shorthand, args.series, index+1+episode_offset)
        episode['destination'] = os.path.join(output_dir, output_name) 
        print 'Job %d: Title: %d, Audio: %d, Duration: %.1fm --> %s\n\tAudio Description: %s' % (index+1, episode['video_track'], episode['audio_track'], episode['duration']/60, episode['destination'], episode['audio_description'])
    print '-' * 40
    if not args.yes:
        print 'Please confirm this looks good'
        response = raw_input().lower()
        if response not in ['y', 'yes']:
            print 'Aborting'
            sys.exit(0)

    print 'Starting Rip'
    for index, episode in enumerate(episodes):
        print 'Job %d: ...' % (index+1)
        handbrake_args = encode_params + ['-i', args.input, '-t', episode['video_track'], '-a', episode['audio_track'], '-o', episode['destination']]
        handbrake_args = map(str, handbrake_args)
        success, stderr = run_handbrake(handbrake_args)
        if not success:
            print 'Failed to complete rip. Aborting the rest of the jobs. Error:', stderr
            break

        #HandBrake doesn't appear to always return an error status on failure, so we will check the results to make sure
        output_duration = get_length(episode['destination'])
        if output_duration == -1:
            print >> 'Unable to check the output duration. Aborting the rest of the jobs'
            break

        if abs(output_duration - episode['duration']) > 1:
            print >> sys.stderr, 'Output and input durations do not match! Input: %fs, Output: %fs. Aborting the rest of the jobs' % (episode['duration'], output_duration)
            break

        print 'Done'
