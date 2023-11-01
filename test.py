from http.server import BaseHTTPRequestHandler, HTTPServer
import mpv
import random
import socket
import threading

from FCastSession import Event, FCastSession, OpCode
from FCastPackets import PlayMessage, PlayBackUpdateMessage, PlayBackState

FCAST_HOST = ''
FCAST_PORT = 46899
FCAST_TIMEOUT = 5000
FCAST_BUFFER_SIZE = 4096
STOP_RECEIVED = False

def handle_play(session: FCastSession, message: PlayMessage):

    if not message:
        print("Got play message without data, ignoring it ...")
        return
    
    print("Got play message for container: %s" % message.container)

    if message.url:
        print("Got play message with URL: %s" % message.url)

        player = mpv.MPV()
        player.play(message.url)
        player.wait_for_playback()

    elif message.content:

        http_port = random.randint(8000, 9000)

        print("Got play message with content, serving it over HTTP on port %d ..." % http_port)

        class http_request_handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header('Content-type', message.container)
                self.end_headers()
                self.wfile.write(message.content.encode())

        http_server = HTTPServer(('', http_port), http_request_handler)

        def run_http_server():
            http_server.serve_forever()

        http_server_thread = threading.Thread(target=run_http_server)
        http_server_thread.start()
        print("Started HTTP server ...")

        player = mpv.MPV()
        player.play("http://localhost:%d/" % http_port)
        player.on_key_press('q', lambda: player.terminate())

        session.send_playback_update(PlayBackUpdateMessage(0, PlayBackState.PLAYING))

        player.wait_for_playback()

        session.send_playback_update(PlayBackUpdateMessage(60, PlayBackState.IDLE))

        print("Waiting for HTTP server to finish ...")
        http_server.shutdown()
        http_server_thread.join()
        print("HTTP server finished")

    else:
        print("Play message has no URL or content, ignoring it ...")

def handle_stop(session: FCastSession, message: None):
    global STOP_RECEIVED

    print("Got stop message")
    STOP_RECEIVED = True

def connection_handler(conn, addr):
    print("Connection from", addr)

    session = FCastSession(conn)

    session.on(Event.PLAY, handle_play)
    session.on(Event.STOP, handle_stop)

    while True:
        
        buff = conn.recv(FCAST_BUFFER_SIZE)
        if not buff or len(buff) <= 0:
            break

        session.process_bytes(buff)

    session.close()
    print("Connection closed from", addr)


s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.settimeout(FCAST_TIMEOUT / 1000)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind((FCAST_HOST, FCAST_PORT))
s.listen()

sessions = []

while not STOP_RECEIVED:
    try:
        conn, addr = s.accept()
        t = threading.Thread(target=connection_handler, args=(conn, addr))
        sessions.append(t)
        t.start()
    except socket.timeout:
        pass

print("Server stopped, waiting for client threads to finish ...")
for session in sessions:
    session.join()

print("All client threads finished, exiting ...")
s.close()
