#!/usr/bin/env swift
// Setup QR code for the Bordnetz app (macOS): endpoint, token, certificate fingerprint and optionally the VRM token
// in one `bordnetz://setup` link. Scan it with the Camera app or in Bordnetz → Settings → Scan Setup Code.
//
//   set -a; . ./local.env; set +a; swift setup-qr.swift [--vrm] [--png file.png]
//
// Reads from the environment: IOS_API_TOKEN, NODE_RED_IP (or BORDNETZ_URL for another address), VRM_TOKEN with --vrm,
// and the GX's certificate from NODE_RED_CA (PEM); without it the certificate is fetched from the GX now and pinned.
// Prints the QR code to the terminal; nothing leaves the Mac. The code holds the token: do not share a picture of it.

import AppKit
import CoreImage
import CryptoKit
import Foundation

func fail(_ message: String) -> Never {
    FileHandle.standardError.write(Data("setup-qr: \(message)\n".utf8))
    exit(1)
}

let environment = ProcessInfo.processInfo.environment
let arguments = CommandLine.arguments.dropFirst()
let withVRM = arguments.contains("--vrm")
let pngPath = arguments.firstIndex(of: "--png").flatMap { arguments.index(after: $0) < arguments.endIndex ? arguments[arguments.index(after: $0)] : nil }

guard let address = environment["BORDNETZ_URL"] ?? environment["NODE_RED_IP"].map({ "https://\($0):1881/api/camper/status" })
else { fail("NODE_RED_IP or BORDNETZ_URL missing (set -a; . ./local.env; set +a)") }
guard let token = environment["IOS_API_TOKEN"], !token.isEmpty else { fail("IOS_API_TOKEN missing") }

/// SHA-256 of the certificate (DER), "AB:12:…" like `openssl x509 -fingerprint -sha256`.
func fingerprint(pem: String) -> String? {
    let base64 = pem.split(separator: "\n").filter { !$0.hasPrefix("-----") }.joined()
    guard let der = Data(base64Encoded: base64) else { return nil }
    return SHA256.hash(data: der).map { String(format: "%02X", $0) }.joined(separator: ":")
}

/// The certificate the server presents now (`openssl s_client`), as PEM.
func serverCertificate(host: String, port: Int) -> String? {
    let process = Process()
    process.executableURL = URL(fileURLWithPath: "/usr/bin/openssl")
    process.arguments = ["s_client", "-connect", "\(host):\(port)", "-servername", host]
    let output = Pipe()
    process.standardInput = FileHandle.nullDevice
    process.standardOutput = output
    process.standardError = FileHandle.nullDevice
    guard (try? process.run()) != nil else { return nil }
    let text = String(decoding: output.fileHandleForReading.readDataToEndOfFile(), as: UTF8.self)
    process.waitUntilExit()
    guard let start = text.range(of: "-----BEGIN CERTIFICATE-----"), let end = text.range(of: "-----END CERTIFICATE-----")
    else { return nil }
    return String(text[start.lowerBound..<end.upperBound])
}

var items = [URLQueryItem(name: "url", value: address), URLQueryItem(name: "token", value: token)]
if address.hasPrefix("https:") {
    let pem: String?
    if let path = environment["NODE_RED_CA"], !path.isEmpty {
        let expanded = NSString(string: path).expandingTildeInPath
        guard let file = try? String(contentsOfFile: expanded, encoding: .utf8) else { fail("no certificate at \(expanded)") }
        pem = file
    } else {
        guard let url = URL(string: address), let host = url.host() else { fail("bad address \(address)") }
        pem = serverCertificate(host: host, port: url.port ?? 443)
    }
    guard let pem, let pin = fingerprint(pem: pem) else { fail("could not read the certificate") }
    items.append(URLQueryItem(name: "pin", value: pin))
}
if withVRM {
    guard let vrm = environment["VRM_TOKEN"], !vrm.isEmpty else { fail("--vrm without VRM_TOKEN") }
    items.append(URLQueryItem(name: "vrm", value: vrm))
}
var components = URLComponents()
components.scheme = "bordnetz"
components.host = "setup"
components.queryItems = items
// Same as SetupLink.url: a `+` in a token must not turn into a space.
components.percentEncodedQuery = components.percentEncodedQuery?.replacingOccurrences(of: "+", with: "%2B")
guard let link = components.string else { fail("could not build the link") }

guard let filter = CIFilter(name: "CIQRCodeGenerator") else { fail("no QR generator") }
filter.setValue(Data(link.utf8), forKey: "inputMessage")
filter.setValue("M", forKey: "inputCorrectionLevel")
guard let code = filter.outputImage else { fail("no QR code") }

// One sample per module: the generator's image is one pixel per module.
let context = CIContext()
let extent = code.extent.integral
let width = Int(extent.width), height = Int(extent.height)
var pixels = [UInt8](repeating: 255, count: width * height)
context.render(code, toBitmap: &pixels, rowBytes: width, bounds: extent, format: .L8, colorSpace: CGColorSpaceCreateDeviceGray())
func dark(_ x: Int, _ y: Int) -> Bool {
    guard x >= 0, y >= 0, x < width, y < height else { return false }
    return pixels[(height - 1 - y) * width + x] < 128 // the bitmap's rows start at the bottom
}

// Terminal: two modules per character (upper and lower half block), with a quiet zone, light on dark terminals too.
let quiet = 2
var text = ""
for row in stride(from: -quiet, to: height + quiet, by: 2) {
    for column in -quiet..<(width + quiet) {
        switch (dark(column, row), dark(column, row + 1)) {
        case (true, true): text += " "
        case (true, false): text += "▄"
        case (false, true): text += "▀"
        case (false, false): text += "█"
        }
    }
    text += "\n"
}
print(text)
print("Bordnetz setup: \(address)\(withVRM ? " + VRM" : "") – scan with the Camera app or in Settings → Scan Setup Code.")

if let pngPath {
    let scaled = code.transformed(by: CGAffineTransform(scaleX: 12, y: 12))
    guard let cgImage = context.createCGImage(scaled, from: scaled.extent),
          let png = NSBitmapImageRep(cgImage: cgImage).representation(using: .png, properties: [:])
    else { fail("could not render the PNG") }
    do { try png.write(to: URL(fileURLWithPath: pngPath)) } catch { fail("could not write \(pngPath): \(error.localizedDescription)") }
    print("PNG: \(pngPath)")
}
