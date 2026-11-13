import { describe, expect, it } from "vitest";
import { labelRef } from "./api";

describe("labelRef (QR label and NFC links)", () => {
  it("reads container and location codes from a path or query", () => {
    expect(labelRef("https://ha.example/x/c/ABC234")).toEqual({ kind: "C", code: "ABC234" });
    expect(labelRef("/l/abc234?x=1")).toEqual({ kind: "L", code: "ABC234" });
    expect(labelRef("https://ha.example/app?c=XYZ789")).toEqual({ kind: "C", code: "XYZ789" });
  });
  it("keeps NFC references exactly as written", () => {
    expect(labelRef("/n/AbCdEfGh12345678_-")).toEqual({ kind: "N", code: "AbCdEfGh12345678_-" });
  });
  it("ignores look-alike characters and other links", () => {
    expect(labelRef("/c/ABC10I")).toBeNull(); // 0, 1 and I aren't used in codes
    expect(labelRef("https://example.com/about")).toBeNull();
  });
});
