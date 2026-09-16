"""Tiny client for the Blender addon listening on 127.0.0.1:45876.
Usage: python bl.py file.py   (or code on stdin)"""
import socket, json, sys, time
def call(cmd, timeout=3600):
    s = socket.create_connection(("127.0.0.1", 45876), timeout=5)
    s.settimeout(timeout)
    s.sendall(json.dumps(cmd).encode())
    buf = b""
    dec = json.JSONDecoder()
    while True:
        chunk = s.recv(1 << 20)
        if not chunk:
            break
        buf += chunk
        try:
            obj, _ = dec.raw_decode(buf.decode("utf-8"))
            s.close()
            return obj
        except ValueError:
            continue
    s.close()
    return json.loads(buf.decode("utf-8"))
if __name__ == "__main__":
    code = open(sys.argv[1], encoding="utf-8").read() if len(sys.argv) > 1 else sys.stdin.read()
    t = time.time()
    r = call({"type": "execute_code", "params": {"code": code}})
    res = r.get("result")
    if isinstance(res, dict) and "result" in res:
        print(res["result"])
    else:
        print(json.dumps(r, indent=1, ensure_ascii=False))
    print("[%.1fs]" % (time.time() - t), file=sys.stderr)
