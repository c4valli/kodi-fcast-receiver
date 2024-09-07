
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread
from typing import cast, Dict, Optional

class FCastHTTPServer(HTTPServer):

    _content: Dict[str, Optional[str]] = {
        'content-type': None,
        'content': None,
    }

    def is_valid_content(self) -> bool:
        if self._content.get('content-type') and self._content.get('content'):
            return True
        else:
            return False

    def set_content(self, content_type: str, content: str) -> None:
        self._content['content-type'] = content_type
        self._content['content'] = content

    def clear_content(self) -> None:
        self._content['content-type'] = None
        self._content['content'] = None

    def get_content(self) -> str:
        return self._content.get('content') or ""
    
    def get_content_type(self) -> str:
        return self._content.get('content-type') or ""

    def get_host(self) -> str:
        return self.host if len(self.host) > 0 else 'localhost'
    
    def get_port(self) -> int:
        return self.port if self.port else int(self.socket.getsockname()[1])
    
    def __init__(self, host: str = '', port: int = 0):

        super().__init__((host, port), FCastWebRequestHandler)

        self.host = host if len(host) > 0 else 'localhost'
        self.port = port if port else int(self.socket.getsockname()[1])

    def start(self):
        self.server_thread = Thread(target=self.serve_forever)
        self.server_thread.start()

    def stop(self):
        server_shutdown_thread = Thread(target=self.shutdown)
        server_shutdown_thread.start()
        server_shutdown_thread.join()

class FCastWebRequestHandler(BaseHTTPRequestHandler):

    def get_fcast_server(self) -> FCastHTTPServer:
        return cast(FCastHTTPServer, self.server)
    
    def do_HEAD(self):
        if not self.get_fcast_server().is_valid_content():
            self.send_error(404, 'Not found')
            return
        
        self.send_response(200)
        self.send_header('Content-Type', self.get_fcast_server().get_content_type())
        self.send_header('Content-Length', str(len(self.get_fcast_server().get_content())))
        self.end_headers()

    def do_GET(self):
        if not self.get_fcast_server().is_valid_content():
            self.send_error(404, 'Not found')
            return

        self.send_response(200)
        self.send_header('Content-Type', self.get_fcast_server().get_content_type())
        self.end_headers()
        self.wfile.write(self.get_fcast_server().get_content().encode('utf-8'))

