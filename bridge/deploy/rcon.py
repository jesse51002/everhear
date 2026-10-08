import socket,struct,sys,re
pw=re.search(r'^rcon.password=(.*)$',open(__file__.rsplit('/',1)[0]+'/server.properties').read(),re.M).group(1)
s=socket.create_connection(('127.0.0.1',25575))
def pkt(i,t,b):
    d=struct.pack('<ii',i,t)+b.encode()+b'\0\0'; s.sendall(struct.pack('<i',len(d))+d)
    n=struct.unpack('<i',s.recv(4))[0]; r=b''
    while len(r)<n: r+=s.recv(n-len(r))
    return r[8:-2].decode()
pkt(1,3,pw); print(pkt(2,2,' '.join(sys.argv[1:])))
