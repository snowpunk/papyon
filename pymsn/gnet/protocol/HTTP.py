# -*- coding: utf-8 -*-
#
# Copyright (C) 2006  Ali Sabil <ali.sabil@gmail.com>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA  02110-1301  USA
#

from gnet.constants import *
from gnet.types import ProxyInfos, HTTPRequest, HTTPResponse
from gnet.io import TCPClient
from gnet.parser import DelimiterParser

import gobject
import base64

__all__ = ['HTTP']

class _HTTPReceiver(object):
    
    CHUNK_START_LINE = 0
    CHUNK_HEADERS = 1
    CHUNK_BODY = 2

    def __init__(self, transport, callback, callback_args=()):
        self._parser = DelimiterParser(transport)
        self._parser.connect("received", self._on_chunk_received)
        self._callback = callback
        self._callback_args = callback_args
        self._reset()

    def _reset(self):
        self._next_chunk = self.CHUNK_START_LINE
        self._receive_buffer = ""
        self._content_length = 0
        self._parser.delimiter = "\r\n"

    def _on_chunk_received(self, parser, chunk):
        complete = False
        if self._next_chunk == self.CHUNK_START_LINE:
            self._receive_buffer += chunk + "\r\n"
            self._next_chunk = self.CHUNK_HEADERS
        elif self._next_chunk == self.CHUNK_HEADERS:
            self._receive_buffer += chunk + "\r\n"
            if chunk == "":
                if self._content_length == 0:
                    complete = True
                else:
                    self._parser.delimiter = self._content_length
                    self._next_chunk = self.CHUNK_BODY
            else:
                header, value = chunk.split(":", 1)
                header, value = header.strip(), value.strip()
                if header == "Content-Length":
                    self._content_length = int(value)
        elif self._next_chunk == self.CHUNK_BODY:
            self._receive_buffer += chunk
            complete = True

        if complete:
            response = HTTPResponse()
            response.parse(self._receive_buffer)
            self._callback(response, *self._callback_args)
            self._reset()


class HTTP(gobject.GObject):
    """HTTP protocol client class."""
    
    __gsignals__ = {
            "error" : (gobject.SIGNAL_RUN_FIRST,
                gobject.TYPE_NONE,
                (gobject.TYPE_ULONG,)),

            "response-received": (gobject.SIGNAL_RUN_FIRST,
                gobject.TYPE_NONE,
                (object,)), # HTTPResponse

            "request-sent": (gobject.SIGNAL_RUN_FIRST,
                gobject.TYPE_NONE,
                (object,)), # HTTPRequest
            }

    def __init__(self, host, port=80, proxy=None):
        """Connection initialization
        
            @param host: the host to connect to.
            @type host: string

            @param port: the port number to connect to
            @type port: integer

            @param proxy: proxy that we can use to connect
            @type proxy: L{gnet.types.ProxyInfos}"""
        gobject.GObject.__init__(self)
        assert(proxy is None or proxy.type == 'http') # TODO: add support for other proxies (socks4 and 5)
        self._host = host
        self._port = port
        self._proxy = proxy
        self._transport = None
        self._http_receiver = None
        self._outgoing_queue = []
        self._waiting_response = False

    def _setup_transport(self):
        if self._transport is None:
            if self._proxy is not None:
                self._transport = TCPClient(self._proxy.host, self._proxy.port)
            else:
                self._transport = TCPClient(self._host, self._port)
            self._http_receiver = _HTTPReceiver(self._transport, self._on_response_received)
            self._transport.connect("notify::status", self._on_status_change)
            self._transport.connect("error", self._on_error)
            self._transport.connect("sent", self._on_request_sent)
        
        if self._transport.get_property("status") != IoStatus.OPEN:
            self._transport.open()

    def _on_status_change(self, transport, param):
        if transport.get_property("status") == IoStatus.OPEN:
            self._process_queue()

    def _on_request_sent(self, transport, request, length):
        assert(str(self._outgoing_queue[0]) == request)
        request = self._outgoing_queue.pop(0)
        self._waiting_response = True
        self.emit("request-sent", request)

    def _on_response_received(self, response):
        self.emit("response-received", response)
        self._waiting_response = False
        self._process_queue() # next request ?

    def _on_error(self, transport, error):
        self.emit("error", error)

    def _process_queue(self):
        if len(self._outgoing_queue) == 0 or \
                self._waiting_response: # no pipelining
            return
        if self._transport is None or \
                self._transport.get_property("status") != IoStatus.OPEN:
            self._setup_transport()
            return
        self._transport.send(str(self._outgoing_queue[0]))

    def request(self, resource='/', headers=None, data='', method='GET'):
        if headers is None:
            headers = {}
        headers['Host'] = self._host + ':' + str(self._port)
        headers['User-Agent'] = GNet.NAME + '/' + GNet.VERSION

        if len(data) > 0:
            headers['Content-Length'] = str(len(data))

        if self._proxy is not None:
            url = 'http://%s:%d%s' % (self._host, self._port, resource)
            if self._proxy.user:
                auth = self._proxy.user + ':' + self._proxy.password
                credentials = base64.encodestring(auth)
                headers['Proxy-Authorization'] = 'Basic ' + credentials
        else:
            url = resource

        request  = HTTPRequest(headers, data, method, resource)
        self._outgoing_queue.append(request)
        self._process_queue()