"""Minimal stand-ins for the Kodi modules, so the add-on can be tested off-device."""

LOGDEBUG, LOGINFO, LOGWARNING, LOGERROR, LOGFATAL = 0, 1, 2, 3, 4

messages = []

def log(msg, level=LOGDEBUG):
    messages.append((level, msg))

builtins_called = []


def executebuiltin(command, wait=False):
    builtins_called.append(command)

def sleep(millis):
    pass

# Tests set jsonrpc_responses[method] to the result Kodi should return, and
# read back jsonrpc_calls to assert on what was asked for.
jsonrpc_calls = []
jsonrpc_responses = {}


def reset_jsonrpc():
    jsonrpc_calls.clear()
    jsonrpc_responses.clear()


def executeJSONRPC(request):
    import json as _json

    parsed = _json.loads(request)
    jsonrpc_calls.append(parsed)

    method = parsed.get("method")
    if method not in jsonrpc_responses:
        return _json.dumps({"jsonrpc": "2.0", "id": parsed.get("id"), "result": []})

    result = jsonrpc_responses[method]
    if isinstance(result, Exception):
        return _json.dumps({
            "jsonrpc": "2.0", "id": parsed.get("id"),
            "error": {"code": -32601, "message": str(result)},
        })
    return _json.dumps({"jsonrpc": "2.0", "id": parsed.get("id"), "result": result})

class Monitor:
    def abortRequested(self):
        return False
    def waitForAbort(self, timeout=0.0):
        return True

class Player:
    def __init__(self, *a, **k):
        self.seeked_to = []
        self.playing = False
        self.time = 0.0
        self.total_time = 0.0

    def isPlaying(self):
        return self.playing

    def getTime(self):
        return self.time

    def getTotalTime(self):
        return self.total_time

    def seekTime(self, seconds):
        self.seeked_to.append(seconds)
        self.time = seconds

    def pause(self):
        pass

    def play(self, *a, **k):
        self.playing = True
