#!/usr/bin/env python
# coding: utf-8
#
# A utility to automate building and installing PSX games onto different
# systems.
# The current directory where you run this utility from needs to be writable
# so that we can use it to store temporary files during the conversing process.

from PIL import Image, ImageDraw, ImageFont
import argparse
import datetime
import io
import os
import re
import random
import struct
import sys
have_pycdlib = False
try:
    import pycdlib
    have_pycdlib = True
except:
    True
have_iso9660 = False
try:
    import iso9660      # python-pycdio
    have_iso9660 = True
except:
    True
try:
    import requests
except:
    print('requests is not installed.\nYou should install requests by running:\npip3 install requests')
try:
    import requests_cache
except:
    print('requests_cache is not installed.\nYou should install requests_cache by running:\npip3 install requests_cache')
import subprocess
import zipfile
from vmp import encode_vmp
from pathlib import Path

from gamedb import games, libcrypt
from bchunk import bchunk
from popstation import popstation, GenerateSFO
from make_isoedat import pack
from ppf import ApplyPPF

temp_files = []  

PSX_SITE = 'https://psxdatacenter.com/'
verbose = False
if sys.platform == 'win32':
    font = 'arial.ttf'
else:
    font = 'DejaVuSansMono.ttf'

def get_gameid_from_iso(path='NORMAL01.iso'):
    if not have_pycdlib and not have_iso9660:
        raise Exception('Can not find either pycdlib or pycdio. Try either \'pip3 install pycdio\' or \'pip3 install pycdlib\'.')

    if have_pycdlib:
        iso = pycdlib.PyCdlib()
        iso.open(path)
        extracted = io.BytesIO()
        iso.get_file_from_iso_fp(extracted, iso_path='/SYSTEM.CNF;1')
        extracted.seek(0)
        buf = str(extracted.read(1024))
        iso.close()
    if have_iso9660:
        iso = iso9660.ISO9660.IFS(source=path)

        st = iso.stat('system.cnf', True)
        if st is None:
            raise Exception('Could not open system.cnf')

        buf = iso.seek_read(st['LSN'])[1][:128]
        iso.close()

    idx = buf.find('cdrom:')
    if idx < 0:
        raise Exception('Could not read system.cnf')

    buf = buf[idx:idx+50]
    idx = buf.find(';1')
    buf = buf[idx-11:idx]
    
    game_id = buf.upper()
    return game_id[:4] + game_id[5:8] + game_id[9:11]


def fetch_cached_file(path):
    ret = requests.get(PSX_SITE + path)
    print('get', PSX_SITE + path) if verbose else None
    if ret.status_code != 200:
        raise Exception('Failed to fetch file ', PSX_SITE + path)

    return ret.content.decode(ret.apparent_encoding)


def fetch_cached_binary(path):
    ret = requests.get(PSX_SITE + path, stream=True)
    if ret.status_code != 200:
        raise Exception('Failed to fetch file ', PSX_SITE + path)

    return ret.content

def get_game_from_gamelist(game_id):
    return fetch_cached_file(games[game_id]['url'])

def get_title_from_game(game_id):
    return games[game_id]['title']

def get_icon0_from_game(game_id, game, cue, tmpfile):
    try:
        image = Image.open(create_path(cue, 'ICON0.PNG'))
        print('Use existing ICON0.PNG as cover') if verbose else None
        return image
    except:
        True

    try:
        url = 'http://www.hwc.nat.cu/psx/' + game_id[0:4] + '_' + game_id[4:7] + '.' + game_id[7:9] + '_COV.jpg'
        print('Try URL', url)
        subprocess.run(['wget', '-q', url, '-O', tmpfile], check=True)
        return Image.open(tmpfile)
    except:
        g = re.findall('images/covers/./.*/.*.jpg', game)
        return Image.open(io.BytesIO(fetch_cached_binary(g[0])))
        
def get_pic1_from_game(game_id, game, cue, filename):
    try:
        image = Image.open(create_path(cue, filename))
        print('Use existing', filename, 'as background') if verbose else None
        return image
    except:
        True

    # Screenshots might be from a different release of the game
    # so we can not use game_id
    filter = 'images/screens/./.*/.*/ss..jpg'
    return Image.open(io.BytesIO(fetch_cached_binary(random.choice(re.findall(filter, game)))))

def get_psio_cover(game_id):
    f = 'https://raw.githubusercontent.com/logi-26/psio-assist/main/covers/' + game_id + '.bmp'
    ret = requests.get(f, stream=True)
    if ret.status_code != 200:
        raise Exception('Failed to fetch file ', f)

    return ret.content

def generate_magic_word(url):
    print('Compute MagicWord from URL', url)
    
    ret = requests.get(url)
    print('get', url) if verbose else None
    if ret.status_code != 200:
        raise Exception('Failed to fetch file ', url)

    b = ret.content.decode(ret.apparent_encoding)
    idx = b.find('Sectors with LibCrypt protection')
    if idx == -1:
        print('Subchannel data not found at', url)
        return 0
    b = b[idx:]
    idx = b.find('table')
    b = b[:idx]

    mw = 0
    if b.find('<td>14105</td>') > 0 or b.find('<td>14110</td>') > 0:
        mw = mw | 0x8000
    if b.find('<td>14231</td>') > 0 or b.find('<td>14236</td>') > 0:
        mw = mw | 0x4000
    if b.find('<td>14485</td>') > 0 or b.find('<td>14490</td>') > 0:
        mw = mw | 0x2000
    if b.find('<td>14579</td>') > 0 or b.find('<td>14584</td>') > 0:
        mw = mw | 0x1000

    if b.find('<td>14649</td>') > 0 or b.find('<td>14654</td>') > 0:
        mw = mw | 0x0800
    if b.find('<td>14899</td>') > 0 or b.find('<td>14904</td>') > 0:
        mw = mw | 0x0400
    if b.find('<td>15056</td>') > 0 or b.find('<td>15061</td>') > 0:
        mw = mw | 0x0200
    if b.find('<td>15130</td>') > 0 or b.find('<td>15135</td>') > 0:
        mw = mw | 0x0100
        
    if b.find('<td>15242</td>') > 0 or b.find('<td>15247</td>') > 0:
        mw = mw | 0x0080
    if b.find('<td>15312</td>') > 0 or b.find('<td>15317</td>') > 0:
        mw = mw | 0x0040
    if b.find('<td>15378</td>') > 0 or b.find('<td>15383</td>') > 0:
        mw = mw | 0x0020
    if b.find('<td>15628</td>') > 0 or b.find('<td>15633</td>') > 0:
        mw = mw | 0x0010
        
    if b.find('<td>15919</td>') > 0 or b.find('<td>15924</td>') > 0:
        mw = mw | 0x0008
    if b.find('<td>16031</td>') > 0 or b.find('<td>16036</td>') > 0:
        mw = mw | 0x0004
    if b.find('<td>16101</td>') > 0 or b.find('<td>16106/td>') > 0:
        mw = mw | 0x0002
    if b.find('<td>16167</td>') > 0 or b.find('<td>16172</td>') > 0:
        mw = mw | 0x0001

    print('MagicWord %04x' % mw)
    return mw
    
def get_first_bin_in_cue(cue):
    with open(cue, "r") as f:
        files = re.findall('".*"', f.read())
        return files[0][1:-1]

def add_image_text(image, title, game_id):
    # Add a nice title text to the background image
    # Split it into separate lines
    #   for ' - '
    print('Add image text: title:', title) if verbose else None
    strings = title.split(' - ')
    y = 18
    txt = Image.new("RGBA", image.size, (255,255,255,0))
    fnt = ImageFont.truetype(font, 20)
    d = ImageDraw.Draw(txt)

    # Add Title (multiple lines) to upper right
    for t in strings:
        ts = d.textsize(t, font=fnt)
        d.text((image.size[0] - ts[0], y), t, font=fnt,
               fill=(255,255,255,255))
        y = y + ts[1] + 2

    # Add game-id to bottom right
    fnt = ImageFont.truetype(font, 10)
    ts = d.textsize(game_id, font=fnt)
    d.rectangle([(image.size[0] - ts[0] - 1, image.size[1] - ts[1] + 1),
                 (image.size[0] + 1, image.size[1] + 1)],
                fill=(0,0,0,255))
    d.text((image.size[0] - ts[0], image.size[1] - ts[1] - 1),
           game_id, font=fnt, fill=(255,255,255,255))

    image = Image.alpha_composite(image, txt)
    return image

def copy_file(inp, oup):
    with open(inp, "rb") as i:
        with open(oup, "wb") as o:
            while True:
                buf = i.read(1024*1024)
                if len(buf) == 0:
                    break
                o.write(buf)


def create_path(bin, f):
    s = bin.split('/')
    if len(s) > 1:
        f = '/'.join(s[:-1]) + '/' + f
    return f

def create_retroarch_thumbnail(dest, game_title, icon0, pic1):
        try:
            os.stat(dest + '/Named_Boxarts')
        except:
            os.mkdir(dest + '/Named_Boxarts')
    
        image = icon0.resize((256,256), Image.BILINEAR)
        #The following characters in playlist titles must be replaced with _ in the corresponding thumbnail filename: &*/:`<>?\|
        f = args.retroarch_thumbnail_dir + '/Named_Boxarts/' + game_title + '.png'
        print('Save cover as', f) if verbose else None
        image.save(f, 'PNG')

        try:
            os.stat(args.retroarch_thumbnail_dir + '/Named_Snaps')
        except:
            os.mkdir(args.retroarch_thumbnail_dir + '/Named_Snaps')
        image = pic1.resize((512,256), Image.BILINEAR)
        #The following characters in playlist titles must be replaced with _ in the corresponding thumbnail filename: &*/:`<>?\|
        f = args.retroarch_thumbnail_dir + '/Named_Snaps/' + game_title + '.png'
        print('Save snap as', f) if verbose else None
        image.save(f, 'PNG')


def create_metadata(img, game_id, game_title, icon0, pic0, pic1):
    print('fetching metadata for', game_id) if verbose else None

    with open(create_path(img, 'GAME_ID'), 'w') as d:
        d.write(game_id)
    with open(create_path(img, 'GAME_TITLE'), 'w') as d:
        d.write(game_title)
    icon0.save(create_path(img, 'ICON0.PNG'))
    pic0.save(create_path(img, 'PIC0.PNG'))
    pic1.save(create_path(img, 'PIC1.PNG'))
        
        
def get_imgs_from_bin(cue):
    def get_file_name(line):
        # strip off leading 'FILE '
        pos = line.lower().index('file ')
        line = line[pos + 5:]
        # strip off leading 'FILE '
        pos = line.lower().index(' binary')
        line = line[:pos+1]
        #strip off leading ' '
        while line[0] == ' ':
            line = line[1:]
        #strip off trailing ' '
        while line[-1] == ' ':
            line = line[:-1]
        # remove double quotes
        if line[0] == '"':
            line = line[1:-1]
        # remove single quotes
        if line[0] == '\'':
            line = line[1:-1]
        return line
    
    print('CUE', cue) if verbose else None

    img_files = []
    with open(cue, 'r') as f:
        lines = f.readlines()
        for line in lines:
            # FILE
            if re.search('^\s*FILE', line):
                f = get_file_name(line)
                s = cue.split('/')
                if len(s) > 1:
                    f = '/'.join(s[:-1]) + '/' + f
                img_files.append(f)
    return img_files


def create_retroarch_bin(dest, game_title, cue_files, img_files):
    try:
        os.mkdir(dest)
    except:
        True
    with open(dest + '/' + game_title + '.m3u', 'wb') as md:
        for i in range(len(img_files)):
            g = game_title
            g = g + '-%d' % i + '.img'
            md.write(bytes(g + chr(13) + chr(10), encoding='utf-8'))

            f = dest + '/' + g
            print('Installing', f) if verbose else None
            copy_file(img_files[i], f)
            

def create_retroarch_cue(dest, game_title, cue_files, img_files):
    try:
        os.mkdir(dest)
    except:
        True
    with open(dest + '/' + 'PSISO.m3u', 'wb') as md:
        for i in range(len(cue_files)):
            p = 'PSISO%d' % i
            with open(dest + '/' + p + '.CD', 'wb') as nc:
                md.write(bytes(p + '.CD' + chr(13) + chr(10), encoding='utf-8'))
                cur_cue = open(cue_files[i], 'r')
                for line in cur_cue:
                    m = re.search('FILE "?(.*?)"? BINARY', line)
                    if m:
                        nc.write(bytes('FILE \"%s.bin\" BINARY' % p + chr(13) + chr(10), encoding='utf-8'))
                    else:
                        nc.write(bytes(line, encoding='utf-8'))
                
                b = dest + '/' + p + '.bin'
                print('Installing', b) if verbose else None
                copy_file(img_files[i], b)


def create_psio(dest, game_id, game_title, icon0, cu2_files, img_files):
    f = dest + '/' + game_title
    try:
        os.mkdir(f)
    except:
        True

    with open(f + '/' + game_id[0:4] + '-' + game_id[4:9] + '.bmp', 'wb') as d:
        image = icon0.resize((80,84), Image.BILINEAR)
        i = io.BytesIO()
        image.save(i, format='BMP')
        i.seek(0)
        d.write(i.read())
            
    try:
        os.unlink(f + '/MULTIDISC.LST')
    except:
        True
    with open(f + '/MULTIDISC.LST', 'wb') as md:
        for i in range(len(img_files)):
            g = game_title
            g = g + '-%d' % i
            g = g + '.img'
            md.write(bytes(g + chr(13) + chr(10), encoding='utf-8'))

            print('Installing', f + '/' + g) if verbose else None
            copy_file(img_files[i], f + '/' + g)
            copy_file(cu2_files[i], f + '/' + g[:-4] + '.cu2')


def get_toc_from_cu2(cu2):
    def bcd(i):
        return int(i % 10) + 16 * (int(i / 10) % 10)

    _toc_header = bytes([
        0x41, 0x00, 0xa0, 0x00, 0x00, 0x00, 0x00, 0x01, 0x20, 0x00,
        0x01, 0x00, 0xa1, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
        0x01, 0x00, 0xa2, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00
        ])
    
    toc = bytearray(_toc_header)

    with open(cu2, 'r') as f:
        lines = f.readlines()

        # Find the number of tracks and trk_end
        num_tracks = None
        trk_end = None
        for line in lines:
            if re.search('^ntracks', line):
                num_tracks = int(line[7:])
            if re.search('^trk end', line):
                trk_end = line[10:]
        # number of tracks
        toc[17] = bcd(num_tracks)
        # size of image
        toc[27] = bcd(int(trk_end[:2]))
        toc[28] = bcd(int(trk_end[3:5]))
        toc[29] = bcd(int(trk_end[6:8]))

        buf = bytearray(10)
        track = 1
        for line in lines:
            if not re.search('^data', line) and not re.search('^track', line):
                continue
            
            msf = line[10:]
            buf[0] = 0x41 if track == 1 else 0x01
            buf[2] = bcd(track)
            buf[7] = bcd(int(msf[:2]))
            buf[8] = bcd(int(msf[3:5]))
            buf[9] = bcd(int(msf[6:8]))
            
            track = track + 1
            toc = toc + buf
            
        return toc


def generate_pbp(dest_file, game_id, game_title, icon0, pic1, cue_files, cu2_files, img_files, aea_files):
    print('Create PBP file for', game_title) if verbose else None

    p = popstation()
    p.verbose = verbose
    p.game_id = game_id
    p.game_title = game_title
    if icon0:
        p.icon0 = icon0
    if pic1:
        p.pic1 = pic1
    if len(aea_files):
        p.aea = aea_files

    for i in range(len(img_files)):
        f = img_files[i]
        toc = p.get_toc_from_ccd(f)
        if not toc:
            print('Need to create a TOC') if verbose else None
            toc = get_toc_from_cu2(cu2_files[i])

        print('Add image', f) if verbose else None
        p.add_img((f, toc))

    p.eboot = dest_file
    print('Create PBP file at', p.eboot)
    p.create_pbp()
    try:
        os.sync()
    except:
        True

    
def create_psp(dest, game_id, game_title, icon0, pic1, cue_files, cu2_files, img_files, mem_cards, aea_files):
    print('Create PSP EBOOT.PBP for', game_title) if verbose else None

    # Convert ICON0 to a file object
    image = icon0.resize((80,80), Image.BILINEAR)
    i = io.BytesIO()
    image.save(i, format='PNG')
    i.seek(0)
    icon0 = i.read()

    # Convert PIC1 to a file object
    pic1 = pic1.resize((480, 272), Image.BILINEAR).convert("RGBA")
    pic1 = add_image_text(pic1, game_title, game_id)
    i = io.BytesIO()
    pic1.save(i, format='PNG')
    i.seek(0)
    pic1 = i.read()
    
    f = dest + '/PSP/GAME/' + game_id
    print('Install EBOOT in', f) if verbose else None
    try:
        os.mkdir(f)
    except:
        True
            
    dest_file = f + '/EBOOT.PBP'
    generate_pbp(dest_file, game_id, game_title, icon0, pic1, cue_files, cu2_files, img_files, aea_files)

    idx = 0
    for mc in mem_cards:
        mf = f + ('/SCEVMC%d.VMP' % idx)
        with open(mf, 'wb') as of:
            print('Installing MemoryCard in temporary location as', mf)
            of.write(encode_vmp(mc))
        idx = idx + 1 
    if idx > 0:
        print('###################################################')
        print('###################################################')
        print('Memory card images temporarily written to the game directory.')
        print('1, Remove the PSP/VITA')
        print('2, Start the game to create the SAVEDATA directory')
        print('   and then quit the game.')
        print('3, Reconnect the PSP/VITA')
        print('4, Run this command to finish installing the memory cards:')
        print('')
        print('./pop-fe.py --psp-dir=%s --game_id=%s --psp-install-memory-card' % (dest, game_id))
        print('###################################################')
        print('###################################################')
        try:
            os.sync()
        except:
            True


def create_ps3(dest, game_id, game_title, icon0, pic0, pic1, cue_files, cu2_files, img_files, mem_cards, aea_files, magic_word, resolution, subdir = './', snd0=None):
    print('Create PS3 PKG for', game_title) if verbose else None

    p = popstation()
    p.verbose = verbose
    p.game_id = game_id
    p.game_title = game_title
    #p.icon0 = icon0
    #p.pic1 = pic1
    p.complevel = 0
    p.magic_word = magic_word
    if len(aea_files):
        p.aea = aea_files
    
    for i in range(len(img_files)):
        f = img_files[i]
        toc = None
        #toc = p.get_toc_from_ccd(f)  # ps3 do not like these tocs
        if not toc:
            print('Need to create a TOC') if verbose else None
            toc = get_toc_from_cu2(cu2_files[i])

        print('Add image', f) if verbose else None
        p.add_img((f, toc))

    # create directory structure
    f = subdir + game_id
    print('GameID', f)
    try:
        os.mkdir(f)
    except:
        True

    sfo = {
        'ANALOG_MODE': {
            'data_fmt': 1028,
            'data': 1},
        'ATTRIBUTE': {
            'data_fmt': 1028,
            'data': 2},
        'BOOTABLE': {
            'data_fmt': 1028,
            'data': 1},
        'CATEGORY': {
            'data_fmt': 516,
            'data_max_len': 4,
            'data': '1P'},
        'PARENTAL_LEVEL': {
            'data_fmt': 1028,
            'data': 3},
        'PS3_SYSTEM_VER': {
            'data_fmt': 516,
            'data_max_len': 8,
            'data': '01.7000'},
        'RESOLUTION': {
            'data_fmt': 1028,
            'data': resolution},
        'SOUND_FORMAT': {
            'data_fmt': 1028,
            'data': 1},
        'TITLE': {
            'data_fmt': 516,
            'data_max_len': 128,
            'data': game_title},
        'TITLE_ID': {
            'data_fmt': 516,
            'data_max_len': 16,
            'data': game_id},
        'VERSION': {
            'data_fmt': 516,
            'data_max_len': 8,
            'data': '01.00'}
        }
    with open(f + '/PARAM.SFO', 'wb') as of:
        of.write(GenerateSFO(sfo))
        temp_files.append(f + '/PARAM.SFO')
    if snd0:
        with open(snd0, 'rb') as i:
            with open(f + '/SND0.AT3', 'wb') as o:
                o.write(i.read())
    
    image = icon0.resize((320, 176), Image.BILINEAR)
    i = io.BytesIO()
    image.save(f + '/ICON0.PNG', format='PNG')
    temp_files.append(f + '/ICON0.PNG')
    
    image = pic0.resize((1000, 560), Image.NEAREST)
    i = io.BytesIO()
    image.save(f + '/PIC0.PNG', format='PNG')
    temp_files.append(f + '/PIC0.PNG')
    
    image = pic1.resize((1920, 1080), Image.NEAREST)
    i = io.BytesIO()
    image.save(f + '/PIC1.PNG', format='PNG')
    temp_files.append(f + '/PIC1.PNG')
    
    image = pic1.resize((310, 250), Image.NEAREST)
    i = io.BytesIO()
    image.save(f + '/PIC2.PNG', format='PNG')
    temp_files.append(f + '/PIC2.PNG')
    
    with open('PS3LOGO.DAT', 'rb') as i:
        with open(f + '/PS3LOGO.DAT', 'wb') as o:
            o.write(i.read())
            temp_files.append(f + '/PS3LOGO.DAT')

    f = subdir + game_id + '/USRDIR'
    try:
        os.mkdir(f)
    except:
        True

    _cfg = bytes([
        0x1c, 0x00, 0x00, 0x00, 0x50, 0x53, 0x31, 0x45,
        0x6d, 0x75, 0x43, 0x6f, 0x6e, 0x66, 0x69, 0x67,
        0x46, 0x69, 0x6c, 0x65, 0x00, 0xe3, 0xb7, 0xeb,
        0x04, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00,
        0xbb, 0xfa, 0xe2, 0x1b, 0x10, 0x00, 0x00, 0x00,
        0x64, 0x69, 0x73, 0x63, 0x5f, 0x6e, 0x6f, 0x00,
        0x04, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
        0x93, 0xd1, 0x5b, 0xf8
    ])
    with open(f + '/CONFIG', 'wb') as o:
        o.write(_cfg)
        temp_files.append(f + '/CONFIG')

        
    f = subdir + game_id + '/USRDIR/CONTENT'
    try:
        os.mkdir(f)
    except:
        True

    p.eboot = subdir + game_id + '/USRDIR/CONTENT/EBOOT.PBP'
    p.iso_bin_dat = subdir + game_id + '/USRDIR/ISO.BIN.DAT'
    try:
        os.unlink(p.iso_bin_dat)
    except:
        True
    print('Create EBOOT.PBP at', p.eboot)
    p.create_pbp()
    temp_files.append(p.eboot)
    temp_files.append(p.iso_bin_dat)
    try:
        os.sync()
    except:
        True

    # sign the ISO.BIN.DAT
    print('Signing', p.iso_bin_dat)
    if os.name == 'posix':
        subprocess.call(['python3', './sign3.py', p.iso_bin_dat])
    else:
        subprocess.call(['sign3.exe', p.iso_bin_dat])

    #
    # USRDIR/SAVEDATA
    #
    f = subdir + game_id + '/USRDIR/SAVEDATA'
    try:
        os.mkdir(f)
    except:
        True
    image = icon0.resize((80,80), Image.BILINEAR)
    i = io.BytesIO()
    image.save(f + '/ICON0.PNG', format='PNG')
    temp_files.append(f + '/ICON0.PNG')    

    if len(mem_cards) < 1:
        create_blank_mc(f + '/SCEVMC0.VMP')
    if len(mem_cards) < 2:
        create_blank_mc(f + '/SCEVMC1.VMP')
    idx = 0
    for mc in mem_cards:
        mf = f + ('/SCEVMC%d.VMP' % idx)
        with open(mf, 'wb') as of:
            print('Installing MemoryCard as', mf)
            of.write(encode_vmp(mc))
        idx = idx + 1 
    temp_files.append(f + '/SCEVMC0.VMP')
    temp_files.append(f + '/SCEVMC1.VMP')

    sfo = {
        'CATEGORY': {
            'data_fmt': 516,
            'data_max_len': 4,
            'data': 'MS'},
        'PARENTAL_LEVEL': {
            'data_fmt': 1028,
            'data': 1},
        'SAVEDATA_DETAIL': {
            'data_fmt': 516,
            'data_max_len': 4,
            'data': ''},
        'SAVEDATA_DIRECTORY': {
            'data_fmt': 516,
            'data_max_len': 4,
            'data': game_id},
        'SAVEDATA_FILE_LIST': {
            'data_fmt': 4,
            'data_max_len': 3168,
            'data': str(bytes(3168))},
        'SAVEDATA_TITLE': {
            'data_fmt': 516,
            'data_max_len': 128,
            'data': ''},
        'TITLE': {
            'data_fmt': 516,
            'data_max_len': 128,
            'data': game_title},
        'SAVEDATA_PARAMS': {
            'data_fmt': 4,
            'data_max_len': 128,
            'data': str(b"A\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\xda\xdaC4\x1br\xc2\xede\xa1/k'D\xc6\x11(\xcf\xc8\xb7(\xb8tG+*f\x85L\nm\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x8a\xfa,\xa1\xe7+mA\xc5m.\x9a\xba\xbct\xb0")}
    }
    with open(f + '/PARAM.SFO', 'wb') as of:
        of.write(GenerateSFO(sfo))
        temp_files.append(f + '/PARAM.SFO')

    #
    # Create ISO.BIN.EDAT
    #
    print('Create ISO.BIN.EDAT')
    pack(subdir + '%s/USRDIR/ISO.BIN.DAT' % game_id,
         subdir + '%s/USRDIR/ISO.BIN.EDAT' % game_id,
         'UP9000-%s_00-0000000000000001' % game_id)
    temp_files.append(subdir + '%s/USRDIR/ISO.BIN.EDAT' % game_id)

    #
    # Create PS3 PKG
    #
    print('Create PKG')
    if os.name == 'posix':
        subprocess.call(['python3','PSL1GHT/tools/ps3py/pkg.py','-c', 'UP9000-%s_00-0000000000000001' % game_id,subdir + game_id, dest])
    else:
        subprocess.call(['pkg.exe','-c', 'UP9000-%s_00-0000000000000001' % game_id,subdir + game_id, dest])
    temp_files.append(subdir + game_id + '/USRDIR/CONTENT')
    temp_files.append(subdir + game_id + '/USRDIR/SAVEDATA')
    temp_files.append(subdir + game_id + '/USRDIR')
    temp_files.append(subdir + game_id)
    print('Finished.', dest, 'created')
    for f in temp_files:
        print('Deleting temp file', f) if verbose else None
        try:
            os.unlink(f)
        except:
            try:
                os.rmdir(f)
            except:
                True

    
def install_psp_mc(dest, game_id, mem_cards):
    if mem_cards and len(mem_cards) >= 1:
        try:
            with open(dest + '/PSP/SAVEDATA/' + game_id + '/SCEVMC0.VMP', 'wb') as f:
                f.write(encode_vmp(mem_cards[0]))
                print('Installed', dest + '/PSP/SAVEDATA/' + game_id + '/SCEVMC0.VMP')
        except:
            raise Exception('Can not install memory card file.', dest + '/PSP/SAVEDATA/' + game_id, 'does not exist')
    if mem_cards and len(mem_cards) >= 2:
        try:
            with open(dest + '/PSP/SAVEDATA/' + game_id + '/SCEVMC1.VMP', 'wb') as f:
                f.write(encode_vmp(mem_cards[1]))
                print('Installed', dest + '/PSP/SAVEDATA/' + game_id + '/SCEVMC1.VMP')
        except:
            raise Exception('Can not install memory card file.', dest + '/PSP/SAVEDATA/' + game_id, 'does not exist')
            
    try:
        os.stat(dest + '/PSP/GAME/' + game_id + '/SCEVMC0.VMP')
        try:
            copy_file(dest + '/PSP/GAME/' + game_id + '/SCEVMC0.VMP',
                      dest + '/PSP/SAVEDATA/' + game_id + '/SCEVMC0.VMP')
            print('Installed', dest + '/PSP/SAVEDATA/' + game_id + '/SCEVMC0.VMP')
            os.unlink(dest + '/PSP/GAME/' + game_id + '/SCEVMC0.VMP')
        except:
            print('Could not install /PSP/SAVEDATA/' + game_id + '/SCEVMC0.VMP')
    except:
        True
        
    try:
        os.stat(dest + '/PSP/GAME/' + game_id + '/SCEVMC1.VMP')
        try:
            copy_file(dest + '/PSP/GAME/' + game_id + '/SCEVMC1.VMP',
                      dest + '/PSP/SAVEDATA/' + game_id + '/SCEVMC1.VMP')
            print('Installed', dest + '/PSP/SAVEDATA/' + game_id + '/SCEVMC1.VMP')
            os.unlink(dest + '/PSP/GAME/' + game_id + '/SCEVMC1.VMP')
        except:
            print('Could not install /PSP/SAVEDATA/' + game_id + '/SCEVMC1.VMP')
    except:
        True
    try:
        os.sync()
    except:
        True

def check_memory_card(f):
    if os.stat(f).st_size == 131072:
        with open(f, 'rb') as mc:
            return [mc.read(131072)]
    if os.stat(f).st_size == 131200:
        with open(f, 'rb') as mc:
            mc.seek(0x80)
            return [mc.read(131072)]
    if os.stat(f).st_size == 131136:
        with open(f, 'rb') as mc:
            mc.seek(0x40)
            return [mc.read(131072)]
    if os.stat(f).st_size == 262144:
        with open(f, 'rb') as mc:
            return [mc.read(131072), mc.read(131072)]
    if os.stat(f).st_size == 134976:
        with open(f, 'rb') as mc:
            mc.seek(0xf40)
            return [mc.read(131072)]
    

def find_psp_mount():
    candidates = ['/d', '/e', '/f', '/g']
    with open('/proc/self/mounts', 'r') as f:
        lines = f.readlines()
        for line in lines:
            strings = line.split(' ')
            if strings[1][:11] == '/run/media/' or strings[1][:7] == '/media/':
                candidates.append(strings[1])
    for c in candidates:
        try:
            os.stat(c + '/PSP/GAME')
            return c
        except:
            True
        try:
            os.stat(c + '/pspemu/PSP/GAME')
            return c + '/pspemu'
        except:
            True
    raise Exception('Could not find any PSP or VITA memory cards')


def create_blank_mc(mc):
    with open(mc, "wb") as f:
        f.seek(131071)
        f.write(bytes(1))
        f.seek(0)
        
        buf = bytearray(2)
        buf[0] = 0x4d
        buf[1] = 0x43
        f.write(buf)

        buf = bytearray([0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
                         0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x0e,
                         0xa0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
                         0xff, 0xff, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
        f.seek(0x70)
        f.write(buf)

        buf = bytearray([0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
                         0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0xa0,
                         0xa0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
                         0xff, 0xff, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
        for i in range(0xf0, 0x780, 0x80):
            f.seek(i)
            f.write(buf)

        buf = bytearray([0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
                         0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0xa0,
                         0xff, 0xff, 0xff, 0xff, 0x00, 0x00, 0x00, 0x00,
                         0xff, 0xff, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
        f.seek(0x7f0)
        f.write(buf)

        buf = bytearray([0xff, 0xff, 0xff, 0xff, 0x00, 0x00, 0x00, 0x00,
                         0xff, 0xff, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
        for i in range(0x880, 0x1190, 0x80):
            f.seek(i)
            f.write(buf)

            
def create_ps2(dest, game_id, game_title, icon0, pic1, cue_files, cu2_files, img_files):
    print('Create PS2 VCD for', game_title) if verbose else None
    print('Install VCD in', dest + '/POPS')

    try:
        os.stat(dest + '/POPS')
    except:
        raise Exception('No POPS directory found')
    try:
        os.stat(dest + '/ART')
    except:
        raise Exception('No ART directory found')
        
    p = popstation()
    p.verbose = verbose
    p.game_id = game_id
    p.game_title = game_title

    discs_txt = None
    vmcdir_txt = None
    if len(img_files) > 1:
        for i in range(4):
            pp = game_id[:4] + '_' + game_id[4:7] + '.' + game_id[7:9] + '.' + game_title
            pp = pp + '_CD%d.VCD\n' % (i + 1)
            if not vmcdir_txt:
                vmcdir_txt = pp[:-5] + '\n'
            if i >= len(img_files):
                pp = '\n'
            if not discs_txt:
                discs_txt = pp
            else:
                discs_txt = discs_txt + pp

    for i in range(len(img_files)):
        f = img_files[i]
        toc = p.get_toc_from_ccd(f)
        if not toc:
            print('Need to create a TOC') if verbose else None
            toc = get_toc_from_cu2(cu2_files[i])

        print('Add image', f) if verbose else None
        p.add_img((f, toc))

        print('GameID', game_id, game_title) if verbose else None
        pp = dest + '/POPS/' + game_id[:4] + '_' + game_id[4:7] + '.' + game_id[7:9] + '.' + game_title
        if len(img_files) > 1:
            pp = pp + '_CD%d' % (i + 1)
        try:
            os.mkdir(pp)
        except:
            True
        p.vcd = pp + '.VCD'
        print('Create VCD at', p.vcd) if verbose else None
        p.create_vcd()
        try:
            os.sync()
        except:
            True

        if discs_txt:
            with open(pp + '/DISCS.TXT', 'w') as f:
                f.write(discs_txt)
        if vmcdir_txt:
            with open(pp + '/VMCDIR.TXT', 'w') as f:
                f.write(vmcdir_txt)


        if i == 0:
            create_blank_mc(pp + '/SLOT0.VMC')
            create_blank_mc(pp + '/SLOT1.VMC')
            
    pp = dest + '/ART/'
    f = pp + game_id[0:4] + '_' + game_id[4:7] + '.' + game_id[7:9] + '_COV.jpg'
    image = icon0.resize((200, 200))
    image = image.convert('RGB')
    image.save(f, format='JPEG', quality=100, subsampling=0)
    f = pp + game_id[0:4] + '_' + game_id[4:7] + '.' + game_id[7:9] + '_BG.jpg'
    image = pic1.resize((640, 480))
    image = image.convert('RGB')
    image.save(f, format='JPEG', quality=100, subsampling=0)


def get_disc_ids(cue_files):
    disc_ids = []
    for idx in range(len(cue_files)):
        print('Convert CUE to a normal style ISO') if verbose else None
        bc = bchunk()
        bc.verbose = args.v
        bc.open(cue_files[idx])
        bc.writetrack(0, 'ISO%02x' % idx)
        temp_files.append('ISO%02x01.iso' % idx)

        gid = get_gameid_from_iso('ISO%02x01.iso' % idx)
        disc_ids.append(gid)

    return disc_ids


def apply_ppf(img, disc_id, magic_word, auto_libcrypt):
    if auto_libcrypt:
        # https://red-j.github.io/Libcrypt-PS1-Protection-bible/index.htm
        print('Try to automatically generate libcrypt patch for', img)
        with open(img, 'rb+') as f:
            while True:
                off = f.tell()
                buf = bytearray(f.read(0x9300))
                if not buf:
                    break
                pos = buf.find(bytes([0x25, 0x30, 0x86, 0x00]))
                if pos > 0:
                    print('Found libcrypt signature. Patching it')
                    struct.pack_into('<H', buf, pos, magic_word)
                    struct.pack_into('<H', buf, pos + 2, 0x34c6)
                    f.seek(off)
                    f.write(buf)
        return
    if 'credit' in libcrypt[disc_id]:
        print(libcrypt[disc_id]['credit'])
    if 'ppf' in libcrypt[disc_id]:
        print('Patching ', disc_id, 'to remove libcrypt')
        ApplyPPF(img, libcrypt[disc_id]['ppf'])
        return
    if not 'ppfzip' in libcrypt[disc_id]:
        print('##################################')
        print('WARNING! No PPF found for', disc_id, 'the game might not work unless you have already patched the image file')
        print('##################################')
        return
    print('Fetching PPF for', disc_id)  if verbose else None
    ret = requests.get(libcrypt[disc_id]['ppfzip'][0])
    if ret.status_code != 200:
        print('##################################')
        print('WARNING! PPF to remove libcrypt was not found for %s. Game might not work.')
        print('##################################')
        return

    z = zipfile.ZipFile(io.BytesIO(ret.content))
    print('Extracting PPF ', libcrypt[disc_id]['ppfzip'][1]) if verbose else None
    z.extract(libcrypt[disc_id]['ppfzip'][1])
    temp_files.append(libcrypt[disc_id]['ppfzip'][1])

    print('Patching ', disc_id, 'to remove libcrypt')
    ApplyPPF(img, libcrypt[disc_id]['ppfzip'][1])

def install_deps():
    print(os.name)
    # requests_cache
    try:
        import requests_cache
        print('requests_cache is already installed')
    except:
        print('Installing python requests_cache')
        subprocess.call(['pip3', 'install', 'requests_cache'])
    # pycdlib
    try:
        import pycdlib
        print('pycdlib is already installed')
    except:
        print('Installing python pycdlib.  This will fail on some platforms')
        subprocess.call(['pip3', 'install', 'pycdlib'])
    # iso9660
    try:
        import iso9660
        print('iso9660 is already installed')
    except:
        print('Installing python iso9660.  This will fail on some platforms')
        subprocess.call(['pip3', 'install', 'iso9660'])
    # ecdsa
    try:
        import ecdsa
        print('ecdsa is already installed')
    except:
        print('Installing python ecdsa')
        subprocess.call(['pip3', 'install', 'ecdsa'])
    # PIL / pillow
    try:
        import pillow
        print('pillow is already installed')
    except:
        print('Installing python pillow')
        subprocess.call(['pip3', 'install', 'pillow']) 
    # pycryptodome
    try:
        import Cryptodome
        print('Crypto/pycryptodome is already installed')
    except:
        print('Trying to install python pycryptodome(Crypto)')
        subprocess.call(['pip3', 'install', 'pycryptodome'])
    # Crypto
    try:
        import Crypto
        print('Crypto is already installed')
    except:
        print('Installing python Crypto')
        subprocess.call(['pip3', 'install', 'Crypto'])
    # cue2cu2
    try:
        if os.name == 'posix':
            os.stat('cue2cu2.py')
            print('cue2cu2.py is already installed')
        else:
            os.stat('cue2cu2.exe')
            print('cue2cu2.py is already installed')
    except:
        print('Downloading cue2cu2.py')
        ret = requests.get('https://raw.githubusercontent.com/NRGDEAD/Cue2cu2/master/cue2cu2.py')
        if ret.status_code != 200:
            print('Failed to download cue2cu2. Aborting install.')
            exit(1)
        if os.name == 'posix':
            with open('cue2cu2.py', 'wb') as f:
                f.write(bytes(ret.content.decode(ret.apparent_encoding), encoding='utf-8'))
        else:
            with open('cue2cu2.exe', 'wb') as f:
                f.write(bytes(ret.content.decode(ret.apparent_encoding), encoding='utf-8'))
    # binmerge
    try:
        os.stat('binmerge')
        print('binmerge is already installed')
    except:
        print('Downloading binmerge')
        ret = requests.get('https://raw.githubusercontent.com/putnam/binmerge/master/binmerge')
        if ret.status_code != 200:
            print('Failed to download binmerge. Aborting install.')
            exit(1)
        with open('binmerge', 'wb') as f:
            f.write(bytes(ret.content.decode(ret.apparent_encoding), encoding='utf-8'))
    if os.name == 'posix':
        # atracdenc
        try:
            os.stat('atracdenc/src/atracdenc')
            print('atracdenc is already installed')
        except:
            print('Cloning atracdenc')
            subprocess.call(['git', 'clone', 'https://github.com/dcherednik/atracdenc.git'])
            os.chdir('atracdenc/src')
            subprocess.call(['cmake', '.'])
            subprocess.call(['make'])
            os.chdir('../..')
        # PSL1GHT
        try:
            os.stat('PSL1GHT')
            print('PSL1GHT is already installed')
        except:
            print('Cloning PSL1GHT')
            subprocess.call(['git', 'clone', 'http://github.com/sahlberg/PSL1GHT'])
            os.chdir('PSL1GHT/tools/ps3py')
            subprocess.call(['git', 'checkout', 'origin/use-python3'])
            subprocess.call(['make'])
            os.chdir('../../..')


# ICON0 is the game cover
# PIC1 is background image/poster
#
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('-v', action='store_true', help='Verbose')
    parser.add_argument('--retroarch-thumbnail-dir',
                    help='Where to store retroarch thumbnails')
    parser.add_argument('--retroarch-bin-dir',
                    help='Where to store retroarch games as (m3u/img)')
    parser.add_argument('--retroarch-cue-dir',
                    help='Where to store retroarch games as (m3u/cue)')
    parser.add_argument('--retroarch-pbp-dir',
                    help='Where to store retroarch games as (pbp)')
    parser.add_argument('--psio-dir',
                    help='Where to store images for PSIO')
    parser.add_argument('--psp-dir',
                    help='Where the PSP memory card is mounted')
    parser.add_argument('--psp-install-memory-card', action='store_true',
                        help='Finish installing a PSX memory card after '
                        'running the game at least once')
    parser.add_argument('--ps2-dir',
                    help='Where the PS2 USB-stick is mounted')
    parser.add_argument('--ps3-pkg',
                    help='Name of the PS3 pckage to create')
    parser.add_argument('--fetch-metadata', action='store_true',
                    help='Just fetch metadata for the game')
    parser.add_argument('--game_id',
                        help='Force game_id for this iso.')
    parser.add_argument('--title',
                    help='Force title for this iso')
    parser.add_argument('--ps3-libcrypt', action='store_true', help='Apply libcrypt patches also for PS3 Packages')
    parser.add_argument('--auto-libcrypt', action='store_true', help='Apply automatically generated libcrypt patches')
    parser.add_argument('--resolution',
                        help='Force setting resolution to 1: NTSC 2: PAL')
    parser.add_argument('--install', action='store_true', help='Install/Build all required dependencies')
    parser.add_argument('--snd0',
                        help='SND0.AT3 file to inject in PS3 PKG')
    parser.add_argument('files', nargs='*')
    args = parser.parse_args()

    if args.v:
        verbose = True

    if args.install:
        print('Install/Update required dependencies')
        install_deps()
        exit(1)

    expire_after = datetime.timedelta(days=100)
    requests_cache.install_cache(str(Path.home()) + '/.pop-fe', expire_after=expire_after)
        
    if args.psp_dir and args.psp_dir.upper() == 'AUTO':
        args.psp_dir = find_psp_mount()

    if not args.files and not args.psp_install_memory_card:
        print('You must specify at least one file to fetch images for')
        exit(1)

    try:
        if os.name == 'posix':
            os.stat('./cue2cu2.py')
        else:
            os.stat('cue2cu2.exe')
    except:
        raise Exception('PSIO prefers CU2 files but cue2cu2.pu is not installed. See README file for instructions on how to install cue2cu2.')

    try:
        os.unlink('NORMAL01.iso')
    except:
        True

    idx = None
    cue_files = []
    cu2_files = []
    img_files = []
    mem_cards = []
    aea_files = {}
    if len(args.files) > 1:
        idx = (1, len(args.files))
    for cue_file in args.files:
        # Try to find which ones are memory cards
        if os.stat(cue_file).st_size <= 262144:
            mc = check_memory_card(cue_file)
            if mc:
                for i in mc:
                    mem_cards.append(i)
                continue
        
        zip = None
        print('Processing', cue_file, '...')

        if cue_file[-3:] == 'zip':
            print('This is a ZIP file. Uncompress the file.') if verbose else None
            zip = cue_file
            with zipfile.ZipFile(zip, 'r') as zf:
                for f in zf.namelist():
                    print('Extracting', f) if verbose else None
                    temp_files.append(f)
                    zf.extract(f)
                    if re.search('.cue$', f):
                        print('Found CUE file', f) if verbose else None
                        cue_file = f

        tmpcue = None
        if cue_file[-3:] == 'img' or cue_file[-3:] == 'bin':
            tmpcue = 'TMP%d.cue' % (0 if not idx else idx[0])
            print('IMG or BIN file. Create a temporary cue file for it', tmpcue) if verbose else None
            temp_files.append(tmpcue)
            with open(tmpcue, "w") as f:
                f.write('FILE "%s" BINARY\n' % cue_file)
                f.write('  TRACK 01 MODE2/2352\n')
                f.write('    INDEX 01 00:00:00\n')

            cue_file = tmpcue

        if cue_file[-3:] != 'cue':
            print('%s is not a CUE file. Skipping' % cue_file) if verbose else None
            continue

        i = get_imgs_from_bin(cue_file)
        img_file = i[0]
        if len(i) > 1:
            try:
                if os.name == 'posix':
                    os.stat('./binmerge')
                else:
                    os.stat('binmerge.exe')
            except:
                raise Exception('binmerge is required in order to support multi-bin disks. See README file for instructions on how to install binmerge.')
            mb = 'MB%d' % (0 if not idx else idx[0])
            if os.name == 'posix':
                subprocess.call(['python3', './binmerge', '-o', '.', cue_file, mb])
            else:
                subprocess.call(['binmerge.exe', '-o', '.', cue_file, mb])
            cue_file = mb + '.cue'
            temp_files.append(cue_file)
            img_file = mb + '.bin'
            temp_files.append(img_file)

        cu2_file = cue_file[:-4] + '.cu2'
        try:
            os.stat(cu2_file).st_size
            print('Using existing CU2 file: %s' % cu2_file) if verbose else None
        except:
            cu2_file = 'TMP%d.cu2' % (0 if not idx else idx[0])
            print('Creating temporary CU2 file: %s' % cu2_file) if verbose else None
            if os.name == 'posix':
                subprocess.call(['python3', './cue2cu2.py', '-n', cu2_file, '--size', str(os.stat(img_file).st_size), cue_file])
            else:
                subprocess.call(['cue2cu2.exe', '-n', cu2_file, '--size', str(os.stat(img_file).st_size), cue_file])
            temp_files.append(cu2_file)

        img_files.append(img_file)
        cue_files.append(cue_file)
        cu2_files.append(cu2_file)

        if args.psp_dir or args.ps3_pkg or args.retroarch_pbp_dir:
            bc = bchunk()
            bc.towav = True
            bc.open(cue_file)
            aea_files[0 if not idx else idx[0] - 1] = []
            for i in range(1, len(bc.cue)):
                if not bc.cue[i]['audio']:
                    continue
                f = 'TRACK_%d_' % (0 if not idx else idx[0])
                bc.writetrack(i, f)
                wav_file = f + '%02d.wav' % (bc.cue[i]['num'])
                temp_files.append(wav_file)
                aea_file = wav_file[:-3] + 'aea'
                temp_files.append(aea_file)
                print('Converting', wav_file, 'to', aea_file)
                try:
                    if os.name == 'posix':
                        subprocess.run(['./atracdenc/src/atracdenc', '--encode=atrac3', '-i', wav_file, '-o', aea_file], check=True)
                    else:
                        subprocess.run(['atracdenc/src/atracdenc', '--encode=atrac3', '-i', wav_file, '-o', aea_file], check=True)
                except:
                    print('XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX\natracdenc not found.\nCan not convert CDDA tracks.\nCreating EBOOT.PBP without support for CDDA audio.\nPlease see README file for how to install atracdenc\nXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX')
                    break
                aea_files[0 if not idx else idx[0] - 1].append(aea_file)

        if idx:
            idx = (idx[0] + 1, idx[1])

    if args.psp_install_memory_card:
        install_psp_mc(args.psp_dir, args.game_id, mem_cards)
        quit()
            
    game_id = None
    if args.game_id:
        game_id = args.game_id
    if not game_id:
        try:
            with open(create_path(args.files[0], 'GAME_ID'), 'r') as d:
                game_id = d.read(9)
        except:
            True

    disc_ids = None
    if len(cue_files) > 1 or not game_id:
        # We need to convert the first track of every ISO so we can open the
        # disk and read system.cnf
        # We only do this for the first disk of a multi-disk set.
        print('Convert CUE to a normal style ISO') if verbose else None
        disc_ids = get_disc_ids(cue_files)
        game_id = disc_ids[0]
    if not disc_ids:
        disc_ids = [game_id]
    game_id = game_id.upper()

    resolution = 1
    if args.ps3_pkg and (game_id[:4] == 'SLES' or game_id[:4] == 'SCES'):
        print('SLES/SCES PAL game. Default resolution set to 2 (640x512)') if verbose else None
        resolution = 2
    if args.resolution:
        print('Resolution set to', args.resolution) if verbose else None
        resolution = int(args.resolution)
    
    game_title = None
    if args.title:
        game_title = args.title
    if not game_title:
        try:
            with open(create_path(args.files[0], 'GAME_TITLE'), 'r') as d:
                game_title = d.read()
        except:
            True
    if not game_title:
        game_title = get_title_from_game(game_id)

    game = get_game_from_gamelist(game_id)

    # ICON0.PNG
    print('Fetch ICON0 for', game_title) if verbose else None
    temp_files.append('ICON0.jpg')
    icon0 = get_icon0_from_game(game_id, game, args.files[0], 'ICON0.jpg')

    # PIC0.PNG
    print('Fetch PIC0 for', game_title) if verbose else None
    pic0 = get_pic1_from_game(game_id, game, args.files[0], 'PIC0.PNG')
    
    # PIC1.PNG
    print('Fetch PIC1 for', game_title) if verbose else None
    pic1 = get_pic1_from_game(game_id, game, args.files[0], 'PIC1.PNG')
    
    print('Id:', game_id)
    print('Title:', game_title)
    print('Cue Files', cue_files) if verbose else None
    print('Imb Files', img_files) if verbose else None
    print('Disc IDs', disc_ids) if verbose else None
    
    magic_word = []
    if game_id in libcrypt:
        for idx in range(len(cue_files)):
            magic_word.append(libcrypt[disc_ids[idx]]['magic_word'])
        patch_libcrypt = False
        if args.auto_libcrypt:
            patch_libcrypt = True
        if args.ps3_pkg and args.ps3_libcrypt:
            patch_libcrypt = True
        if args.psp_dir or args.ps2_dir or args.psio_dir:
            print('#####################################')
            print('WARNING! This disc is protected with libcrypt.')
            print('Will attempt to apply libcrypt PPF patch')
            print('#####################################')
            patch_libcrypt = True
        if args.ps3_pkg:
            print('#####################################')
            print('WARNING! This disc is protected with libcrypt.')
            print('Will attempt to inject MagicWord into ISO.BIN.DAT')
            print('This should work for most games. If not then try')
            print('creating the package again with --ps3-libcrypt')
            print('#####################################')
        if patch_libcrypt:
            #
            # Copy the CUE and BIN locally so we can patch them
            for idx in range(len(cue_files)):
                i = get_imgs_from_bin(cue_files[idx])
                print('Copy %s to LCP%02x.bin so we can patch libcrypt' % (i[0], idx)) if verbose else None
                copy_file(i[0], 'LCP%02x.bin' % idx) 
                temp_files.append('LCP%02x.bin' % idx)
                with open(cue_files[idx], 'r') as fi:
                    l = fi.readlines()
                    l[0] = 'FILE "%s" BINARY\n' % ('LCP%02x.bin' % idx)
                    with open('LCP%02x.cue' % idx, 'w') as fo:
                        fo.writelines(l)
                    temp_files.append('LCP%02x.cue' % idx)
                cue_files[idx] = 'LCP%02x.cue' % idx
                img_files[idx] = 'LCP%02x.bin' % idx
                apply_ppf(img_files[idx], disc_ids[idx], magic_word[idx], args.auto_libcrypt)

    if args.psp_dir:
        create_psp(args.psp_dir, game_id, game_title, icon0, pic1, cue_files, cu2_files, img_files, mem_cards, aea_files)
    if args.ps2_dir:
        create_ps2(args.ps2_dir, game_id, game_title, icon0, pic1, cue_files, cu2_files, img_files)
    if args.ps3_pkg:
        create_ps3(args.ps3_pkg, game_id, game_title, icon0, pic0, pic1, cue_files, cu2_files, img_files, mem_cards, aea_files, magic_word, resolution, snd0=args.snd0)
    if args.fetch_metadata:
        create_metadata(img_files[0], game_id, game_title, icon0, pic0, pic1)
    if args.psio_dir:
        create_psio(args.psio_dir, game_id, game_title, icon0, cu2_files, img_files)
    if args.retroarch_bin_dir:
        new_path = args.retroarch_bin_dir + '/' + game_title
        create_retroarch_bin(new_path, game_title, cue_files, img_files)
    if args.retroarch_cue_dir:
        new_path = args.retroarch_cue_dir + '/' + game_title
        create_retroarch_cue(new_path, game_title, cue_files, img_files)
    if args.retroarch_pbp_dir:
        new_path = args.retroarch_pbp_dir + '/' + game_title + '.pbp'
        if icon0:
            image = icon0.resize((80,80), Image.BILINEAR)
            i = io.BytesIO()
            image.save(i, format='PNG')
            i.seek(0)
            icon0 = i.read()

        if pic1:
            image = pic1
            i = io.BytesIO()
            image.save(i, format='PNG')
            i.seek(0)
            pic1 = i.read()
        
        generate_pbp(new_path, game_id, game_title, icon0, pic1, cue_files, cu2_files, img_files, aea_files)
    if args.retroarch_thumbnail_dir:
        create_retroarch_thumbnail(args.retroarch_thumbnail_dir, game_title, icon0, pic1)

    for f in temp_files:
        print('Deleting temp file', f) if verbose else None
        try:
            os.unlink(f)
        except:
            try:
                os.rmdir(f)
            except:
                True
