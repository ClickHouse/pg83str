#!/usr/bin/env python3
"""A workload split by size: which sizes a trace's time comes from.

    dev/lab/split.py IN OUT [--traces memset,memcpy] [--edges 0,16,32,64,128,256,512,1024,2048,4096]

Reads a workload (workload.py's format), writes one with the same pools and,
per trace named, one trace per size bucket of its records (lo < n <= hi; the
first bucket from 0 inclusive) named TRACE/lo-hi. kbench runs a trace NAME/...
for NAME's primitive: per bucket, the kernels' ns per call on those calls
alone, in the order the trace has them.
"""
import argparse
import struct
from pathlib import Path

REC = struct.Struct('<B3xIQQ')


def read(path):
    b = Path(path).read_bytes()
    assert b[:4] == b'STRW'
    version, ssz, dsz, nt = struct.unpack_from('<IQQI', b, 4)
    assert version == 1
    o = 4 + struct.calcsize('<IQQI')
    pools = b[o:o + ssz + dsz]
    o += ssz + dsz
    traces = []
    for _ in range(nt):
        nl, = struct.unpack_from('<I', b, o)
        name = b[o + 4:o + 4 + nl].decode()
        count, = struct.unpack_from('<Q', b, o + 4 + nl)
        o += 4 + nl + 8
        traces.append((name, b[o:o + count * REC.size]))
        o += count * REC.size
    return ssz, dsz, pools, traces


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('inp')
    ap.add_argument('out')
    ap.add_argument('--traces', default='memset,memcpy')
    ap.add_argument('--edges', default='0,16,32,64,128,256,512,1024,2048,4096')
    args = ap.parse_args()
    edges = [int(x) for x in args.edges.split(',')]
    ssz, dsz, pools, traces = read(args.inp)
    out = []
    for name, recs in traces:
        if name not in args.traces.split(','):
            continue
        for lo, hi in zip(edges, edges[1:]):
            keep = [recs[i:i + REC.size] for i in range(0, len(recs), REC.size)
                    if (lo < REC.unpack_from(recs, i)[1] <= hi or (lo == edges[0] and REC.unpack_from(recs, i)[1] == lo))]
            if keep:
                out.append(('%s/%d-%d' % (name, lo, hi), b''.join(keep)))
    with open(args.out, 'wb') as f:
        f.write(b'STRW' + struct.pack('<IQQI', 1, ssz, dsz, len(out)) + pools)
        for name, recs in out:
            f.write(struct.pack('<I', len(name)) + name.encode() + struct.pack('<Q', len(recs) // REC.size) + recs)
    for name, recs in out:
        print('%-20s %6d calls' % (name, len(recs) // REC.size))


if __name__ == '__main__':
    main()
