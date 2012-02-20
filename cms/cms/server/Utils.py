#!/usr/bin/python
# -*- coding: utf-8 -*-

# Programming contest management system
# Copyright © 2010-2012 Giovanni Mascellani <mascellani@poisson.phc.unipi.it>
# Copyright © 2010-2012 Stefano Maggiolo <s.maggiolo@gmail.com>
# Copyright © 2010-2012 Matteo Boscariol <boscarim@hotmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

"""Random utilities for web servers.

"""

import os
import traceback
import time

import tarfile
import zipfile

from functools import wraps
from tornado.web import HTTPError

from cms.util.Cryptographics import decrypt_number
from cms.service.FileStorage import FileCacher
from cms.service.LogService import logger


def valid_phase_required(func):
    """Decorator that rejects requests outside the contest phase.

    """
    @wraps(func)
    def newfunc(self, *args, **kwargs):
        if self.r_params["phase"] != 0:
            self.redirect("/")
        else:
            return func(self, *args, **kwargs)
    return newfunc


def catch_exceptions(func):
    """Decorator to catch all errors originating from a function. If
    an error is detected, it writes to the browser and conclude the
    request.

    """
    @wraps(func)
    def newfunc(self, *args, **kwargs):
        try:
            return func(self, *args, **kwargs)
        except HTTPError:
            # These errors are generated by the code (such as 404),
            # they're expected and tornado will take care of them.
            raise
        except Exception as error:
            logger.critical("Uncaught exception (%r) while processing "
                            "a request: %s" % (error, traceback.format_exc()))
            self.write("A critical error has occurred :-(")
            self.finish()
    return newfunc


def decrypt_arguments(func):
    """Decorator that decrypts all arguments.

    """
    @wraps(func)
    def newfunc(self, *args, **kwargs):
        # We reply with Forbidden if the given ID cannot be decrypted.
        new_args = []
        for arg in args:
            try:
                new_args.append(decrypt_number(arg))
            except ValueError:
                logger.warning("User %s called with undecryptable argument." %
                               self.current_user.username)
                raise HTTPError(403)
        new_kwargs = {}
        for k in kwargs:
            try:
                new_kwargs[k] = decrypt_number(kwargs[k])
            except ValueError:
                logger.warning("User %s called with undecryptable argument." %
                               self.current_user.username)
                raise HTTPError(403)
        return func(self, *new_args, **new_kwargs)
    return newfunc


def extract_archive(temp_name, original_filename):
    """Obtain a list of files inside the specified archive.

    Returns a list of the files inside the archive located in
    temp_name, using original_filename to guess the type of the
    archive.

    """
    file_list = []
    if original_filename.endswith(".zip"):
        try:
            zip_object = zipfile.ZipFile(temp_zip_filename, "r")
            for item in zip_object.infolist():
                file_list.append({
                    "filename": item.filename,
                    "body": zip_object.read(item)})
        except Exception as error:
            return None
    elif original_filename.endswith(".tar.gz") \
        or original_filename.endswith(".tar.bz2") \
        or original_filename.endswith(".tar"):
        try:
            tar_object = tarfile.open(name=temp_name)
            for item in tar_object.getmembers():
                if item.isfile():
                    file_list.append({
                        "filename": item.name,
                        "body": tar_object.extractfile(item).read()})
        except tarfile.TarError as error:
            return None
        except IOError as error:
            return None
    else:
        return None
    return file_list

def file_handler_gen(BaseClass):
    """This generates an extension of the BaseHandler that allows us
    to send files to the user. This *Gen is needed because the code in
    the class FileHandler is exactly the same (in AWS and CWS) but
    they inherits from different BaseHandler.

    BaseClass (class): the BaseHandler of our server.
    returns (class): a FileHandler extending BaseClass.

    """
    class FileHandler(BaseClass):
        """Base class for handlers that need to serve a file to the user.

        """
        def fetch(self, digest, content_type, filename):
            """Sends the RPC to the FS.

            """
            if digest == "":
                logger.error("No digest given")
                self.finish()
                return
            try:
                self.temp_filename = self.application.service.FC.get_file(
                    digest, temp_path=True)
            except Exception as error:
                logger.error("Exception while retrieving file `%s'. %r" %
                             (filename, error))
                self.finish()
                return

            self.set_header("Content-Type", content_type)
            self.set_header("Content-Disposition",
                            "attachment; filename=\"%s\"" % filename)
            self.start_time = time.time()
            self.size = 0
            self.temp_file = open(self.temp_filename, "rb")
            self.application.service.add_timeout(self._fetch_write_chunk,
                                                 None, 0.02,
                                                 immediately=True)

        def _fetch_write_chunk(self):
            """Send a chunk of the file to the browser.

            """
            data = self.temp_file.read(FileCacher.CHUNK_SIZE)
            l = len(data)
            self.size += l / 1024.0 / 1024.0
            self.write(data)
            if l < FileCacher.CHUNK_SIZE:
                self.temp_file.close()
                os.unlink(self.temp_filename)
                duration = time.time() - self.start_time
                logger.info("%.3lf seconds for %.3lf MB, %.3lf MB/s" %
                            (duration, self.size, self.size / duration))
                self.finish()
                return False
            return True

    return FileHandler
