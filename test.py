from http.server import BaseHTTPRequestHandler, HTTPServer
import mpv
import random
import socket
import threading

from resources.lib.fcast_plugin.FCastSession import Event, FCastSession, OpCode
from resources.lib.fcast_plugin.FCastPackets import PlayMessage, PlayBackUpdateMessage, PlayBackState, SeekMessage

FCAST_HOST = ''
FCAST_PORT = 46899
FCAST_TIMEOUT = 5000
FCAST_BUFFER_SIZE = 32000
STOP_RECEIVED = False

player: mpv.MPV = None

def handle_play(session: FCastSession, message: PlayMessage):
    global player
    if not message:
        print("Got play message without data, ignoring it ...")
        return
    
    print("Got play message for container: %s" % message.container)

    if player:
        player.stop()

    play_url = ''

    if message.url:
        print("Got play message with URL: %s" % message.url)

        player = mpv.MPV()
        play_url = message.url

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
        play_url = "http://localhost:%d/" % http_port

    else:
        print("Play message has no URL or content, ignoring it ...")
    
    if player:
        global last_time_update
        last_time_update = None
        player.on_key_press('q', lambda: player.terminate())
        @player.property_observer('time-pos')
        def time_observer(_name, value):
            global last_time_update
            global player
            val_int = int(value or 0)
            if value is not None and last_time_update != val_int:
                last_time_update = val_int
                session.send_playback_update(
                    PlayBackUpdateMessage(
                        val_int,
                        PlayBackState.PAUSED if player.pause else PlayBackState.PLAYING
                    ),
                )
        @player.property_observer('pause')
        def watch_pause(_name, value):
            global player
            time = player._get_property('time-pos')
            if time is not None:
                session.send_playback_update(
                    PlayBackUpdateMessage(
                        int(time),
                        PlayBackState.PAUSED if player.pause else PlayBackState.PLAYING),
                    )
        @player.event_callback('end_file')
        def playback_ended():
            print("Waiting for HTTP server to finish ...")
            http_server.shutdown()
            http_server_thread.join()
            print("HTTP server finished")
        # Start MPV
        player.play(play_url)

def handle_pause(session: FCastSession, message: None):
    global player
    player.pause = True

def handle_resume(session: FCastSession, message: None):
    global player
    player.pause = False

def handle_stop(session: FCastSession, message: None):
    global STOP_RECEIVED

    print("Got stop message")
    STOP_RECEIVED = True
    player.stop()

def handle_seek(session: FCastSession, message: SeekMessage):
    global player
    player.seek(message.time, reference='absolute', precision='exact')

def connection_handler(conn, addr):
    print("Connection from", addr)

    session = FCastSession(conn)

    session.on(Event.PLAY, handle_play)
    session.on(Event.PAUSE, handle_pause)
    session.on(Event.RESUME, handle_resume)
    session.on(Event.SEEK, handle_seek)
    session.on(Event.STOP, handle_stop)

    while True:
        print("Waiting for next client packet")
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
