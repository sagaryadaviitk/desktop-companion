"""HTTP server binding without reverse DNS on numeric loopback listeners."""

from http.server import ThreadingHTTPServer
import ipaddress
from socketserver import TCPServer


class LoopbackHTTPServer(ThreadingHTTPServer):
    def server_bind(self):
        try:
            numeric_loopback = ipaddress.ip_address(self.server_address[0]).is_loopback
        except ValueError:
            numeric_loopback = False
        if not numeric_loopback:
            return super().server_bind()
        # HTTPServer.server_bind calls socket.getfqdn after binding, which can
        # block on DNS even though our listeners use a numeric loopback address.
        TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]
