# Barcode lookup providers

Order of lookup:

1. **Your own inventory**, an exact match on the normalised GTIN-14. UPC-A `012345678905`, its EAN-13 form `0012345678905` and the UPC-E equivalent all match the same product. The result reads, for example, "I found this product in your inventory. You own 3: 2 in Box 15, Shed › Right side; 1 in Garage › Upper shelving."
2. **Cache**: catalog hits are kept for 90 days, misses for 7 days. Lookups that fail transiently are not cached.
3. **External catalogs**, each switchable in the App configuration.

A barcode without a catalog result is normal. You type the details, and the barcode is stored so the next scan finds it in your inventory.

## Evaluation

| Provider | Cost | Key | Rate limit | Coverage | Licence / terms | Default |
|---|---|---|---|---|---|---|
| **Open Food Facts** | Free | No | ~100 product reads/min per IP | Excellent for groceries, worldwide | ODbL (attribution, share-alike for the database) | On |
| **Open Products Facts** | Free | No | Same | Small but growing (household goods, electronics) | ODbL | On |
| **Open Beauty Facts** | Free | No | Same | Cosmetics and toiletries | ODbL | On |
| **UPCitemdb** (trial endpoint) | Free | No | 100 requests/day, 6/min | Good for North American retail: tools, electronics, household | Commercial terms; trial for low-volume use | On |
| **Barcode Lookup** (barcodelookup.com) | Paid (from about US$99/month) | Yes | By plan | Very broad | Commercial | Off (enable with `barcode_lookup_api_key`) |
| Go-UPC, EAN-Search.org | Paid | Yes | By plan | Broad | Commercial | Not implemented. The provider list in `services/barcode.py` is easy to extend. |

For a household (a few hundred scans a year), the free providers plus your own growing inventory are usually enough. Tools and electronics are where UPCitemdb helps most.

## Privacy

Only the barcode number is sent. Requests identify the App with a generic User-Agent, as Open Food Facts asks. No personal data is included.

## Supported symbologies (camera)

UPC-A, UPC-E, EAN-13, EAN-8, ITF-14 (GTIN-14), Code 128 and QR (container labels). Manual entry is always available. Check digits are validated, and UPC-E is expanded to UPC-A.
