// One write to the REAL X1700: SET_CUR clock selector 7 -> pin 1 (what usbaudiod sends), bracketed by GET_CUR reads.
// Class request to the AudioControl interface; nothing touches flash. usage: probe_selector_write
import Foundation
import IOKit
import IOUSBHost

let m = IOServiceMatching("IOUSBHostDevice") as NSMutableDictionary
m["idVendor"] = 0x154E; m["idProduct"] = 0x500A
let svc = IOServiceGetMatchingService(kIOMainPortDefault, m)
guard svc != 0 else { print("X1700 not found"); exit(1) }
let dev = try IOUSBHostDevice(__ioService: svc, options: [], queue: nil, interestHandler: nil)

func send(_ label: String, _ type: UInt8, _ req: UInt8, _ val: UInt16, _ idx: UInt16, _ payload: [UInt8], _ len: UInt16) {
    let r = IOUSBDeviceRequest(bmRequestType: type, bRequest: req, wValue: val, wIndex: idx, wLength: len)
    let data = NSMutableData(bytes: payload, length: payload.count)
    if type & 0x80 != 0 { data.length = Int(len) }
    var n = 0
    do { try dev.__send(r, data: data, bytesTransferred: &n, completionTimeout: 1.0)
         let hex = (data as Data).prefix(n).map { String(format: "%02x", $0) }.joined(separator: " ")
         print("\(label): OK  \(n) bytes \(hex)") }
    catch { print("\(label): STALL/FAIL \((error as NSError).code)") }
}
send("1 GET_CUR selector (before)", 0xA1, 1, 0x0100, 0x0700, [], 1)
send("2 SET_CUR selector -> 1     ", 0x21, 1, 0x0100, 0x0700, [1], 1)
send("3 GET_CUR selector (after) ", 0xA1, 1, 0x0100, 0x0700, [], 1)
