import sys
import struct


def read_boxes(data, start=0, end=None):
    if end is None:
        end = len(data)

    result = []

    while start + 8 <= end:
        size = struct.unpack(">I", data[start:start+4])[0]
        typ = data[start+4:start+8].decode("latin1")
        header = 8

        if size == 1:
            if start + 16 > end:
                break
            size = struct.unpack(">Q", data[start+8:start+16])[0]
            header = 16

        elif size == 0:
            size = end - start

        if size < header or start + size > end:
            break

        result.append((typ, start, size, header))
        start += size

    return result


def find_box(data, wanted):
    found = []

    def scan(start, end):
        for typ, offset, size, header in read_boxes(data, start, end):

            if typ == wanted:
                found.append(
                    (offset, size, data[offset:offset+size])
                )

            if typ in ("moof", "traf"):
                scan(
                    offset + header,
                    offset + size
                )

    scan(0, len(data))
    return found


def parse_tfdt(box):
    version = box[8]

    if version == 1:
        decode_time = struct.unpack(">Q", box[12:20])[0]
    else:
        decode_time = struct.unpack(">I", box[12:16])[0]

    return version, decode_time


def parse_trun(box):
    flags = struct.unpack(">I", box[8:12])[0]
    sample_count = struct.unpack(">I", box[12:16])[0]

    return flags, sample_count


def inspect(filename):
    data = open(filename, "rb").read()

    tfdt = find_box(data, "tfdt")
    trun = find_box(data, "trun")

    result = {}

    if tfdt:
        result["tfdt_version"], result["decode_time"] = parse_tfdt(tfdt[0][2])

    if trun:
        result["trun_flags"], result["sample_count"] = parse_trun(trun[0][2])

    return result


if len(sys.argv) != 3:
    print("Usage:")
    print("python compare_timing.py file1.mp4 file2.mp4")
    sys.exit(1)


file1 = sys.argv[1]
file2 = sys.argv[2]

a = inspect(file1)
b = inspect(file2)

print("=" * 70)
print("DASH TIMING / SAMPLE STRUCTURE")
print("=" * 70)

print("\nFILE 1:", file1)
print("FILE 2:", file2)

for key in (
    "tfdt_version",
    "decode_time",
    "trun_flags",
    "sample_count",
):

    print("\n" + key)

    print("File 1:", a.get(key, "NOT FOUND"))
    print("File 2:", b.get(key, "NOT FOUND"))

    if key in a and key in b:
        print("Same  :", a[key] == b[key])

print("\n" + "=" * 70)
print("DONE")
print("=" * 70)