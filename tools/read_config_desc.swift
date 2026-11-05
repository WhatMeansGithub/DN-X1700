// Read-only: fetch the live configuration descriptor (GET_DESCRIPTOR) from the X1700 and print key bytes. usage: read_config_desc [out.bin]
import Foundation
import IOKit
import IOUSBHost
let m = IOServiceMatching("IOUSBHostDevice") as NSMutableDictionary
m["idVendor"] = 0x154E; m["idProduct"] = 0x500A
let svc = IOServiceGetMatchingService(kIOMainPortDefault, m)
guard svc != 0 else { print("X1700 not found"); exit(1) }
let dev = try IOUSBHostDevice(__ioService: svc, options: [], queue: nil, interestHandler: nil)
let req = IOUSBDeviceRequest(bmRequestType: 0x80, bRequest: 6, wValue: 0x0200, wIndex: 0, wLength: 385)
let data = NSMutableData(length: 385)!
var n = 0
try dev.__send(req, data: data, bytesTransferred: &n, completionTimeout: 1.0)
let b = [UInt8](data as Data)
print("read \(n) bytes")
for (name, off) in [("IAD0 class        (cfg+13) ", 13), ("AS1 bmControls    (cfg+140)", 140), ("EP 0x01 bmAttrib  (cfg+161)", 161),
                    ("AS2 bmControls    (cfg+195)", 195), ("EP 0x85 bmAttrib  (cfg+216)", 216)] {
    print(String(format: "%@ = 0x%02x", name, b[off]))
}
if CommandLine.arguments.count > 1 { try (data as Data).write(to: URL(fileURLWithPath: CommandLine.arguments[1])) }
