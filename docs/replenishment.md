# Restocking and shopping

Home Inventory works out what you're running low on and builds a shopping list, grouped by shop. It **never buys anything, never changes a retailer cart by itself, and never claims something is in your cart** unless you confirm it.

## Setting up an item

On an item, open the **Restock** section:
- **Minimum.** Below this the item is "running low". This is the same threshold as the low-stock alert.
- **Restock to.** Suggested purchases fill up to this level.
- **Buy at least** (optional) sets a minimum purchase quantity. **Unit** is also optional.
- **Where to buy.** Add one or more shops (Amazon, Lowe's, Tractor Supply, or any other shop) with the product page link, the shop's product number, pack size and an estimated price. One shop is *preferred*.

**Suggested quantity:** if stock is below the restock level, buy the larger of (restock level − stock) and *buy at least*, rounded up to whole packs. For example, with stock 1, minimum 2 and restock level 5, the suggestion is 4.

Stock counts every household holding of the product. Customer equipment and items marked missing are left out.

## The Restock page

- **Needs restocking** shows items below the minimum, then items getting low, each with a suggested quantity, preferred shop and estimated cost. Tap **+ 4** to add an item to the list.
- **Shopping list** is grouped by shop. Select some items, or use all of them, then tap **Open at <shop>**.
  - **Amazon:** a single *Review & add* link opens Amazon's own Add to Cart page with the items and quantities. Amazon asks you to confirm before anything goes into your cart.
  - **Lowe's, Tractor Supply and other shops:** each item opens its product page, or a search on the shop's site if you only gave a product number. The quantity is shown and can be copied. You add it to the cart yourself.
- Opened items show **Opened at shop**. Once they really are in your cart, tap **It's in my cart**.
- Tap **Bought** to record the purchase, optionally with the price. It can add the quantity to your stock straight away.
- **Later** moves an item off the active list without forgetting it.

Barcode scanning: scan something you already own, then tap **Add to shopping list**.

## What each shop allows (researched November 2026)

| Capability | Amazon | Lowe's | Tractor Supply |
|---|---|---|---|
| Open the exact product page | Yes (`/dp/<ASIN>` or your link) | Yes (your link) | Yes (your link) |
| Search by product number or name | Yes | Yes | Yes |
| Hand items to the cart for you to confirm | **Yes**: the documented Add to Cart form (`/gp/aws/cart/add.html?ASIN.1=…&Quantity.1=…`) | No | No |
| Add to the cart directly (API) | No | No | No |
| Read your cart | No | No | No |
| Prices and stock by API | Only for approved Associates via the Creators API; not used | No public API | No public API |

**Why not more?**
- **Amazon** removed cart operations from its Product Advertising API in version 5, and that API has since been retired. Its successor, the Creators API, needs an approved, selling Associates account and has no cart operations. The Add to Cart form is Amazon's documented way to hand items to a shopper's cart. You can set your own Associates tag in Settings, but you don't need to.
- **Lowe's** offers APIs to approved business partners only; there is no self-serve developer access. Its affiliate program offers links, not cart access.
- **Tractor Supply** runs an affiliate program (Partnerize/Pepperjam) with links and feeds for approved publishers, but no public API.

Browser automation was ruled out. Logging in to retailer sites as you would need your password or cookies stored in Home Assistant. It would also break retailers' terms and anti-bot protections, and fail unpredictably.

Sources:
- [Amazon PA-API 5: Add to Cart form](https://webservices.amazon.com/paapi5/documentation/add-to-cart-form.html)
- [Amazon Associates help: Cart API removed in PA-API 5](https://affiliate-program.amazon.com/help/node/topic/GUVFJTV7MGMMNY94)
- [PA-API retirement and the Creators API](https://blog.freshstore.com/amazon-creators-api-pa-api-retirement/)
- [Lowe's APIs are for authorised partners](https://www.projectsforce.com/post/lowe-s-api-explained-how-contractors-automate-orders-and-job-data)
- [Tractor Supply affiliate program](https://www.tractorsupply.com/tsc/cms/policies-information/affiliate-program)

## Adding another shop

Each shop is a `Provider` in `services/retailers.py`. It declares its capabilities (product link, search link, cart hand-off, cart API, cart inspection, price lookup, availability) and builds its links. The restock logic doesn't change when a shop is added, and the app shows only the capabilities a shop declares.
