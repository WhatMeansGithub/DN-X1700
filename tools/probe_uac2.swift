// Read-only UAC2 GET probe via IOUSBHost (same API usbaudiod uses). Sends only bmRequestType 0xA1 GET_CUR/GET_RANGE.
import Foundation
import IOKit
import IOUSBHost

func find() -> io_service_t {
    let m = IOServiceMatching("IOUSBHostDevice") as NSMutableDictionary
    m["idVendor"] = 0x154E; m["idProduct"] = 0x500A
    return IOServiceGetMatchingService(kIOMainPortDefault, m)
}
let svc = find()
guard svc != 0 else { print("X1700 not found"); exit(1) }
let dev: IOUSBHostDevice
do { dev = try IOUSBHostDevice(__ioService: svc, options: [], queue: nil, interestHandler: nil) }
catch { print("open failed: \(error)"); exit(2) }
print("opened IOUSBHostDevice (no capture)")

let reqs: [(String, UInt8, UInt16, UInt16, UInt16)] = [
    ("baseline: clock source 5 sample rate GET_CUR", 1, 0x0100, 0x0500, 4),
    ("baseline: clock source 5 sample rate GET_RANGE", 2, 0x0100, 0x0500, 64),
    ("baseline: clock source 5 valid GET_CUR", 1, 0x0200, 0x0500, 1),
    ("baseline: clock selector 7 GET_CUR", 1, 0x0100, 0x0700, 1),
    ("usbaudiod #1: ent 3 cs 7 (latency) GET_CUR len 4", 1, 0x0700, 0x0300, 4),
    ("usbaudiod #2: ent 1 cs 7 (latency) GET_CUR len 4", 1, 0x0700, 0x0100, 4),
    ("usbaudiod #3: ent 2 cs 2 GET_CUR len 6 (cluster)", 1, 0x0200, 0x0200, 6),
    ("usbaudiod #4: itf 1 cs 2 GET_CUR len 5", 1, 0x0200, 0x0001, 5),
    ("usbaudiod #5: itf 1 cs 2 GET_CUR len 1", 1, 0x0200, 0x0001, 1),
    ("usbaudiod #6: itf 1 cs 2 GET_CUR len 2", 1, 0x0200, 0x0001, 2),
    ("usbaudiod #7: itf 1 cs 2 GET_CUR len 3", 1, 0x0200, 0x0001, 3),
]
for (label, r, v, i, l) in reqs {
    let req = IOUSBDeviceRequest(bmRequestType: 0xA1, bRequest: r, wValue: v, wIndex: i, wLength: l)
    let data = NSMutableData(length: Int(l))!
    var n = 0
    var res: String
    do { try dev.__send(req, data: data, bytesTransferred: &n, completionTimeout: 1.0)
         res = "OK  \(n) bytes: " + (data as Data).prefix(n).map { String(format: "%02x", $0) }.joined(separator: " ") }
    catch { res = "FAIL \(error)" }
    print(String(format: "a1 %02x %04x %04x %04x  ", r, v, i, l) + label.padding(toLength: 52, withPad: " ", startingAt: 0) + " -> " + res)
}
