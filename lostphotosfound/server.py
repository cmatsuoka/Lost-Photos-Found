#!/usr/bin/env python
# -*- coding: utf-8 -*-
# same license, author and
# credits of the main script

import os

# to avoid image duplicates
import hashlib

# for the mime list (all images extensions)
import mimetypes

# for the index
import shelve

# to build the mail object and pickle fields
from email import message_from_bytes
from email.utils import parsedate

# the working man (should we connect to IMAP as a read-only client btw?)
from imapclient import IMAPClient

# to parse the sender email address
import email

from lostphotosfound.utils import _app_folder
from lostphotosfound.utils import _charset_decoder

class Server:
    """
    Server class to fetch and filter data

    Connects to the IMAP server, search according to a criteria,
    fetch all attachments of the mails matching the criteria and
    save them locally with a timestamp
    """
    def __init__(self, host, username, password, search='', label='',
                 debug=False, use_index=True, use_folders=False):
        """
        Server class __init__ which expects an IMAP host to connect to

        @param host: gmail's default server is fine: imap.gmail.com
        @param username: your gmail account (i.e. obama@gmail.com)
        @param password: we highly recommend you to use 2-factor auth here
        """

        if not host:
            raise Exception('Missing IMAP host parameter in your config')

        try:
            self._server = IMAPClient(host, use_uid=True, ssl=True)
        except:
            raise Exception('Could not successfully connect to the IMAP host')

        setattr(self._server, 'debug', debug)

        # mails index to avoid unnecessary redownloading
        index = '.index_%s' % (username)
        index = os.path.join(_app_folder(), index)
        self._index = shelve.open(index, writeback=True)

        # list of attachments hashes to avoid dupes
        hashes = '.hashes_%s' % (username)
        hashes = os.path.join(_app_folder(), hashes)
        self._hashes = shelve.open(hashes, writeback=True)

	# additional email filtering using Gmail search syntax
        self._search = search

        # use a different default label
        self._label = label

	# ignore index file
        self._use_index = use_index

        # create folders by sender
        self._use_folders = use_folders

        self._username = username
        self._login(username, password)

    def _login(self, username, password):
        """
        Login to the IMAP server and selects the all mail folder

        @param username: your gmail account (i.e. obama@gmail.com)
        @param password: we highly recommend you to use 2-factor auth here
        """

        if not username or not password:
            raise Exception('Missing username or password parameters')

        try:
            self._server.login(username, password)
        except Exception:
            raise Exception('Cannot login, check username/password, are you using 2-factor auth?')

        # gmail's allmail folder always has the '\\AllMail' flag set
        # regardless of the user language in gmail's settings
        if self._label:
            all_mail = self._label
        else:
            for flags, delimiter, folder_name in self._server.xlist_folders():
                if b'\\AllMail' in flags:
                    all_mail = folder_name
                    break

        # stats logging
        print("LOG: selecting message folder '{}'".format(all_mail))
        try:
            self._server.select_folder(all_mail, readonly=True)
        except IMAPClient.Error:
            raise Exception('Cannot select the folder {}, please verify its name'.format(all_mail))

    def _filter_messages(self):
        """Filter mail to only parse ones containing images"""

        # creates a list of all types of image files to search for,
        # even though we have no idea if gmail supports them or what
        mimetypes.init()
        mimes = []
        for ext in mimetypes.types_map:
            if 'image' in mimetypes.types_map[ext]:
                mimes.append(ext.replace('.', ''))
        mimelist = ' OR '.join(mimes)

        # that's why we only support gmail
        # for other mail services we'd have to translate the custom
        # search to actual IMAP queries, thus no X-GM-RAW cookie for us
        criteria = 'has:attachment filename:(%s)' % (mimelist)

        # add user-defined search criteria
        if self._search:
            criteria = '{} {}'.format(self._search, criteria)

        try:
            messages = self._server.gmail_search(criteria)
        except:
            raise Exception('Search criteria returned a failure, it must be a valid gmail search')

        # stats logging
        print("LOG: {} messages matched the search criteria {}".format(len(messages), criteria))
        return messages

    def _save_part(self, part, mail, sender):
        """
        Internal function to decode attachment filenames and save them all

        @param mail: the mail object from message_from_string so it can checks its date
        @param part: the part object after a mail.walk() to get multiple attachments
        """

        if not hasattr(self, "seq"):
            self.seq = 0

        # we check if None in filename instead of just if it is None
        # due to the type of data decode_header returns to us
        name = part.get_filename()
        if name is None:
            name = "__unnamed__"
        header_filename = _charset_decoder(name)

        # i.e. some inline attachments have no filename field in the header
        # so we have to hack around it and get the name field
        if 'None' in header_filename:
            header_filename = part.get('Content-Type').split('name=')[-1].replace('"', '')
        elif not header_filename[0][0] or header_filename[0][0] is None:
            # we should hopefully never reach this, attachments would be 'noname' in gmail
            header_filename = 'attachment-%06d.data' % (self.seq)
            self.seq += 1

        # sanitize it
        punct = '!"#$&\'*+/;<>?[\]^`{|}~'
        table = str.maketrans(dict.fromkeys(punct))
        header_filename = header_filename.translate(table)

        # 2012-10-28_19-15-22 (Y-M-D_H-M-S)
        header_date = parsedate(_charset_decoder(mail['date']))
        header_date = '%s-%s-%s_%s-%s-%s_' % (header_date[0],
                                              header_date[1],
                                              header_date[2],
                                              header_date[3],
                                              header_date[4],
                                              header_date[5])
        filename = header_date + header_filename

        # we should create it in the documents folder
        username = self._username
        userdir = os.path.expanduser('~/LostPhotosFound')
        savepath = os.path.join(userdir, username)

        # create sub-folders for senders if user told us to
        if self._use_folders:
            savepath = os.path.join(savepath, sender)

        if not os.path.isdir(savepath):
            os.makedirs(savepath)

        # logging complement
        print("\t...{}".format(filename))

        saved = os.path.join(savepath, filename)
        if not os.path.isfile(saved):
            with open(saved, 'wb') as imagefile:
                try:
                    payload = part.get_payload(decode=True)
                except:
                    message = 'Failed when downloading attachment: %s' % (saved)
                    raise Exception(message)

                payload_hash = hashlib.sha1(payload).hexdigest()
                # gmail loves to duplicate attachments in replies
                if payload_hash not in self._hashes.keys():
                    try:
                        imagefile.write(payload)
                    except:
                        message = 'Failed writing attachment to file: %s' % (saved)
                        raise Exception(message)
                    self._hashes[payload_hash] = payload_hash
                else:
                    print("Duplicated attachment {} ({})".format(saved, payload_hash))
                    os.remove(saved)

    def _cleanup(self):
        """Gracefully cleans up the mess and leave the server"""

        self._index.sync()
        self._index.close()
        self._hashes.sync()
        self._hashes.close()
        self._server.close_folder()
        self._server.logout()

    def lostphotosfound(self):
        """The actual program, which fetchs the mails and all its parts attachments"""

        messages = self._filter_messages()

        for msg in messages:
            try:
                idfetched = self._server.fetch([msg], ['X-GM-MSGID'])
            except:
                raise Exception('Could not fetch the message ID, server did not respond')

            keys = list(idfetched.keys());
            msgid = str(idfetched[keys[0]][b'X-GM-MSGID'])

            # mail has been processed in the past, skip it
            if self._use_index and msgid in self._index.keys():
                print("Skipping X-GM-MSDID {}".format(msgid))
                continue

            # if it hasn't, fetch it and iterate through its parts
            msgdata = self._server.fetch([msg], ['RFC822'])

            for data in msgdata:
                mail = message_from_bytes(msgdata[data][b'RFC822'])

                if mail.get_content_maintype() != 'multipart':
                    continue

                # logging
                header_from = _charset_decoder(mail['From'])

                if mail["Subject"]:
                    header_subject = _charset_decoder(mail["Subject"])
                else:
                    header_subject = "no_subject"

                print('[{}]: {}'.format(header_from, header_subject))

		# use raw header, header_from sometimes excludes the email address
                sender = email.utils.parseaddr(mail["From"])[1]
                if not sender:
                    sender = "unknown_sender"

                for part in mail.walk():
                    # if it's only plain text, i.e. no images
                    if part.get_content_maintype() == 'multipart':
                        continue
                    # if no explicit attachments unless they're inline
                    if part.get('Content-Disposition') is None:
                        pass
                    # if non-graphic inline data
                    if 'image/' not in part.get_content_type():
                        continue

                    # only then we can save this mail part
                    self._save_part(part, mail, sender)

                # all parts of mail processed, add it to the index
                self._index[msgid] = msgid

        self._cleanup()
