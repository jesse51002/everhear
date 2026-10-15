import fcntl,os,socket,struct,sys,re
# One RCON command: python3 rcon.py <command…>. The server answers every RCON connection from
# one buffer, so the command holds /tmp/vfg-rcon-<port>.lock (VFG_RCON_LOCK) from send to
# reply, as the engine (vfg_engine.mc.rcon) and vfg (remote/common.py) do.
props=open(__file__.rsplit('/',1)[0]+'/server.properties').read()
pw=re.search(r'^rcon.password=(.*)$',props,re.M).group(1)
m=re.search(r'^rcon.port=(\d+)',props,re.M); port=int(m.group(1)) if m else 25575
lock=os.environ.get('VFG_RCON_LOCK') or '/tmp/vfg-rcon-%d.lock'%port
try: fd=os.open(lock,os.O_RDONLY)
except FileNotFoundError: fd=os.open(lock,os.O_RDONLY|os.O_CREAT,0o666)
s=socket.create_connection(('127.0.0.1',port))
def pkt(i,t,b):
    d=struct.pack('<ii',i,t)+b.encode()+b'\0\0'; s.sendall(struct.pack('<i',len(d))+d)
    n=struct.unpack('<i',s.recv(4))[0]; r=b''
    while len(r)<n: r+=s.recv(n-len(r))
    return r[8:-2].decode()
pkt(1,3,pw); fcntl.flock(fd,fcntl.LOCK_EX); print(pkt(2,2,' '.join(sys.argv[1:])))
