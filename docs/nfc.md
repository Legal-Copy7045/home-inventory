# NFC tags

NFC stickers work alongside QR labels. Tap a tag with your phone to open an item, container, location or kit, or to go straight to an action such as *Log hours*, *Log service*, *Move*, *Use one* or *Start repair job*.

## How it works

Each tag stores one link: `https://<your Home Assistant>/<app panel>/n/<reference>`.

- The **reference** is random: 22 characters, 128 bits. It contains no database ID, Home Assistant token or Ingress session.
- A tag **grants nothing.** Opening the link goes through Home Assistant's normal login, and the App looks up the reference only for a logged-in user. Unknown, deactivated and lost tags all give the same "Tag not recognised" answer.
- The tag points at the **record, not a place,** so it keeps working when the item or box moves.

## What phones can do (researched November 2026)

| | Android (Chrome) | iPhone (iOS) | Home Assistant Companion app |
|---|---|---|---|
| Open a link tag by tapping | Yes. Android shows the link and opens it in the browser. | Yes, natively, with no app (iPhone XS and later; shown as a notification). | Not used for these tags. |
| Read a tag inside the web app | Yes, with **Web NFC**, but only when the app is open in its own browser tab. Web NFC is limited to top-level pages, so it's unavailable inside Home Assistant's frame. | No (Safari has no Web NFC). | No (its web view has no Web NFC). |
| Write a tag from the web app | Yes, with Web NFC, in its own tab. | No. Use an NFC writer app such as *NFC Tools* and write the link as a URL record. | No. Its own tag writer writes Home Assistant tag IDs, not our links. |
| Chip serial number (duplicate detection) | Yes, with Web NFC. | Not available. | — |

Background tag reading by a web page isn't possible on any platform. The phone's operating system reads the link and opens it.

## Assigning a tag

1. Open the item, container, location or kit, then go to **NFC tags → Assign tag**.
2. Optionally name it (e.g. "Handle") and choose what happens when it's scanned.
3. Choose how to write the tag:
   - **Android:** tap *Write tag with this phone* and hold the tag to the back of the phone. If the app is inside Home Assistant, first tap *Open the app in its own tab*. The chip's serial number is checked so the same sticker can't be assigned twice.
   - **iPhone or other devices:** tap *Get the link*, then copy it or scan its QR code, and write it with an NFC app as a **URL** record.
4. Scan the tag to try it.

## Managing tags

The **NFC tags** page lists active, inactive and lost tags. From there you can:
- change what a tag does when scanned;
- deactivate or reactivate a tag;
- **replace** a lost or damaged tag (the old one stops working and you get a new link);
- show the link and QR code, to print as a fallback label.

To reassign a tag to a different record, use the API's `PATCH /tags/{id}`.

## Home Assistant

Each scan fires `home_inventory_tag_scanned` with `tag_id`, `target_type`, `target_id`, `default_action` and `user`. The reference itself is never broadcast.
