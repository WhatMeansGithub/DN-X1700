// Writes to the REAL X1700: SET_CUR sample rate on clock sources 5 and 6, writing back the value GET_CUR reports
// (what Linux snd-usb-audio does when setting a rate). Bracketed by GET_CUR reads. Nothing touches flash.
// usage: probe_rate_write
import Foundation
import IOKit
import IOUSBHost

let m = IOServiceMatching("IOUSBHostDevice") as NSMutableDictionary
m["idVendor"] = 0x154E; m["idProduct"] = 0x500A
let svc = IOServiceGetMatchingService(kIOMainPortDefault, m)
guard svc != 0 else { print("X1700 not found"); exit(1) }
let dev = try IOUSBHostDevice(__ioService: svc, options: [], queue: nil, interestHandler: nil)

@discardableResult
func send(_ label: String, _ type: UInt8, _ req: UInt8, _ val: UInt16, _ idx: UInt16, _ payload: [UInt8], _ len: UInt16) -> [UInt8]? {
    let r = IOUSBDeviceRequest(bmRequestType: type, bRequest: req, wValue: val, wIndex: idx, wLength: len)
    let data = NSMutableData(bytes: payload, length: payload.count)
    if type & 0x80 != 0 { data.length = Int(len) }
    var n = 0
    do { try dev.__send(r, data: data, bytesTransferred: &n, completionTimeout: 1.0)
         let b = Array((data as Data).prefix(n))
         print("\(label): OK  \(n) bytes \(b.map { String(format: "%02x", $0) }.joined(separator: " "))")
         return b }
    catch { print("\(label): STALL/FAIL \((error as NSError).code)"); return nil }
}
for clk: UInt16 in [5, 6] {
    let idx = clk << 8
    let cur = send("clock \(clk) GET_CUR (before)", 0xA1, 1, 0x0100, idx, [], 4)
    var v: [UInt8] = clk == 5 ? [0x44, 0xac, 0, 0] : [0x80, 0xbb, 0, 0]
    if let c = cur, c.count == 4, c != [0xff, 0xff, 0xff, 0xff] { v = c }
    send("clock \(clk) SET_CUR \(v.map { String(format: "%02x", $0) }.joined())", 0x21, 1, 0x0100, idx, v, 4)
    send("clock \(clk) GET_CUR (after) ", 0xA1, 1, 0x0100, idx, [], 4)
}
