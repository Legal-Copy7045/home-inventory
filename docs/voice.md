# Asking "where is…?" by voice

The companion integration adds a Home Assistant intent, **`HomeInventoryWhereIs`**, with one slot, **`item`**. It answers out loud with the best one to three places, for example:

> "Hedge trimmer is in Shed, Box #3."
> "Ladder is lent to Sam."
> "Saw is in Garage for now, normally Shed."

Anything that can hand Home Assistant an intent can use it. You need the integration installed and set up; the App itself never has to be reachable from outside your home.

| Voice front end | Works? | What you set up |
|---|---|---|
| **Home Assistant Assist** (companion apps, Wear OS, voice satellites, the Assist dialog) | Yes | A small custom-sentences file (below) |
| **Siri** | Yes, through Assist | An Apple Shortcut that passes your words to Assist |
| **Alexa** ("Alexa, ask Home Inventory where the hedge trimmer is") | Yes, with an Alexa *custom skill* | An Amazon developer skill + Home Assistant's Alexa intent support; Home Assistant must be reachable by Amazon over HTTPS |
| Google Assistant | Not directly | Google's Home Assistant integration controls devices only; use Assist on Android instead |

## 1. Assist (recommended)

Copy [`deploy/assist/custom_sentences/en/home_inventory.yaml`](../deploy/assist/custom_sentences/en/home_inventory.yaml) to `custom_sentences/en/home_inventory.yaml` in your Home Assistant configuration folder (next to `configuration.yaml`; create the folders if they don't exist). It holds every Home Inventory question:

| Intent | Example | Answers with |
|---|---|---|
| `HomeInventoryWhereIs` | "Where is the hedge trimmer?" | where it is (or asks which one) |
| `HomeInventoryWhereDoesItGo` | "Where does the hammer go?", "Tell me where to put the engine oil" | where it belongs, for putting it back (and where it is now, if it's out) |
| `HomeInventoryMove` | "I've moved the engine oil to small engine repair", "…to container number 5" | moves it there, asking which one or "did you mean…?" when unclear |
| `HomeInventoryWhatsIn` | "What's in box 5?", "What's on the top shelf?" | the things, boxes and places recorded there |
| `HomeInventoryChoose` | "The second one", "yes", "no" | the answer for that one |
| `HomeInventoryMaintenanceDue` | "What maintenance is due?" | overdue jobs and those due in two weeks |
| `HomeInventoryRunningLow` | "What's running low?" | supplies below their minimum |
| `HomeInventoryOnLoan` | "What's lent out?" | what's on loan, and to whom |

Add your own phrasings to it if you like; keep the intent names.

Restart Home Assistant (or reload conversation). Then say or type "Where is the hedge trimmer?" to Assist.

If your words match several different things, it asks: *"I found 3 things for hose: Kink Control Plus Water Hose 50ft, Siphon pump hose and Siphon pump with hose and funnel. Which one did you mean?"* Answer with "the water hose one", "the siphon pump" or "the second one" (within two minutes). Voice devices keep listening after a question, so you can just answer.

**Putting things back.** "Where does the hammer go?", "Tell me where to put the engine oil", "Where should I put my drill back?" or "Where does the jigsaw belong?" answer with the thing's usual place: *"Hammer goes in Garage, Tool wall."* If it isn't there right now, that's added: *"Saw goes in Shed. It's in Kitchen for now."*, *"Drill goes in Shed. Sam has it at the moment."* This works with Assist, Siri (through Assist) and, from 0.22.1, Alexa ("Alexa, ask Home Inventory where the hammer goes", "…to tell me where to put the engine oil"). After updating, copy the new sentences file again, and update the Alexa skill (below).

**Telling it you've moved something.** "I've moved the engine oil to small engine repair", "I put the drill in the garage", "The hedge trimmer is now in the shed", or with Alexa "Alexa, tell Home Inventory that I've moved the engine oil to container number 5". The place can be a location's name ("small engine repair", "the top shelf") or a container by number ("container number 5", "box five") or name. Spoken names don't have to be exact.

- A clear match is moved straight away: *"Done. I've moved Engine oil to Small Engine Repair, in Shed."* Back to its usual place: *"Done. Engine oil is back in…"*.
- One likely place that isn't certain: *"Did you mean Small Engine Repair, in Shed?"*. Say **yes** to move it, **no** to leave it, or say the right place.
- Several possible places (two "Top shelf"s): *"Which place: Top shelf, in Shed or Top shelf, in Garage?"*. Answer "the garage one" or "the first one".
- Several things fit the item ("tin snips"), or the same thing is kept in two places: it asks which first.
- The new place becomes the item's place (a permanent move). It is recorded in Activity history; to move it back, say so the same way, or use Move on the item page.

Moving needs a Home Inventory member (not a viewer); the voice assistant uses the Home Assistant user it runs as.

**What's in a box or on a shelf.** "What's in box 5?", "What's on the top shelf?", "Alexa, ask Home Inventory what's in container number 12". Places are found the same forgiving way as for moving. It reads out what's recorded there (the first five of each kind and how many more), and asks which one if two places have the same name.

**See it on your phone ("show me").** With answers sent to your phone (the integration's options), a notification for an item with a photo shows the photo, so you can spot it on a crowded shelf. Tap it to open Home Assistant.

**Names it didn't recognise.** **Admin → Voice history** lists names asked for that weren't found ("strimmer"), with the items they probably meant. **Another name for String trimmer** adds it, and voice and search find the item by that name from then on.

Three more questions need no item name: **"What maintenance is due?"** (overdue jobs and those due in the next two weeks), **"What's running low?"** (supplies below their minimum) and **"What's lent out?"** (and to whom). Long lists read the first five and say how many more.

Other languages: put a file with the same intent name in `custom_sentences/<language>/`, with sentences in that language.

If you use an AI conversation agent (OpenAI, Gemini…) with Assist, you can also expose the `home_inventory.get_item_location` action through a script (see `home-assistant.md`) so the agent can look things up.

### How names are matched

Names are compared in a forgiving but careful way:

- Case, punctuation and extra spaces don't matter, and a leading "the" or "my" is ignored.
- Plurals match singulars: "wood screws" = "Wood screw", "batteries" = "Battery", "shelves" = "Shelf".
- Words that are singular but end in "s" are left alone: "Glass", "Compass", "Status", "Chassis".
- Words that are only ever plural keep their meaning: "Safety glasses" is not "Safety glass", and "Pliers" stays "Pliers".
- Sizes and model numbers are compared as written:
  - "50 ft", "50 feet", "50-foot" and "50'" are the same.
  - "2.5 mm" is not "25 mm".
  - "HRX-217" = "HRX 217" = "HRX217".
- Hyphens, apostrophes and "&" don't matter: "self-propelled" = "self propelled", "Pat's bike" = "Pats bike".

If two different things still match, it asks which one you meant rather than guessing.

## 2. Siri

Siri can't call Home Assistant intents by itself, but it can run an Apple Shortcut that sends text to Assist using the Home Assistant companion app:

1. In the **Shortcuts** app, create a shortcut named **"Where is"**.
2. Add **Dictate Text** (or **Ask for Input**).
3. Add the Home Assistant app's **Assist** action and pass it the text `where is` followed by the dictated text.
4. Add **Speak Text** with the Assist response (if your companion-app version doesn't speak it already).

Then: "Hey Siri, Where is" → "hedge trimmer" → Siri answers. Siri needs the two-step exchange because shortcuts can't take a free-text parameter in the same phrase.

## 3. Alexa custom skill

"Alexa, ask Home Inventory where the hedge trimmer is" needs your own private Alexa *custom skill*. It stays in development mode, so it is never published, and it works on every Echo signed in to your Amazon account.

How it fits together: Alexa → a small forwarding function (`deploy/alexa/lambda_function.py`) → Home Assistant's `/api/alexa` → the Home Inventory intent → spoken answer. The forwarder is needed because `/api/alexa` requires a Home Assistant access token, which Alexa can't send by itself.

**The trade-off:** Amazon has to reach your Home Assistant over HTTPS from the internet, through Home Assistant Cloud's remote URL or your own domain. The Home Inventory App itself stays private, and only Home Assistant is reached, using the token you create.

You need:
- Home Assistant reachable over HTTPS from the internet;
- a free Amazon developer account (the same Amazon account as your Echo);
- Home Inventory 0.10.0 or later, with the integration updated in HACS.

### A. Home Assistant

1. **Turn on Alexa custom-skill support.** Add one line to `configuration.yaml` and restart Home Assistant:

   ```yaml
   alexa:
   ```

2. **Create a user for Alexa (recommended).** Settings → People → Users → Add user, for example "Alexa". Leave it **not** an administrator.
3. **Create a long-lived access token for that user.** Log in as that user, then Profile → Security → Long-lived access tokens → Create token. Copy it now; it's shown only once.
4. **Note your external address.** For example `https://xxxxxxxx.ui.nabu.casa` or `https://ha.example.com`, with no `/api` at the end.

### B. Create the skill

5. Go to developer.amazon.com → **Alexa Skills Kit** → **Create Skill**. Fill in:
   - Name: **Home Inventory**
   - Primary locale: your Echo's language, e.g. English (UK)
   - Experience: **Other** → **Custom**
   - Hosting: **Alexa-hosted (Python)**, which is simplest because it needs no AWS account, in the region nearest you. Alternatively choose **Provision your own** to use your own AWS Lambda (step 8b).
6. Open **Interaction Model → JSON Editor**. (On the "Choose a template" screen before this, pick **Start from Scratch**.) Replace everything with the contents of `deploy/alexa/interaction-model.json`, then **Save** and **Build skill**.
7. Copy the **Skill ID** from the skill list ("Copy Skill ID"). It looks like `amzn1.ask.skill.…`.

### C. Add the forwarder

8a. **Alexa-hosted skill:**
   1. Open the **Code** tab and replace the contents of `lambda_function.py` with `deploy/alexa/lambda_function.py`.
   2. Alexa-hosted skills have no environment variables, so fill in the three settings near the top of the file (`HA_URL`, `HA_TOKEN`, `SKILL_ID`) instead of the `os.environ.get(...)` defaults.
   3. **Save**, then **Deploy**.
   4. The code, including the token, stays private to your developer account. Don't share or publish the skill.

8b. **Your own AWS Lambda instead:**
   1. Create a Python 3.12 function in the region Alexa uses for your locale: US East (N. Virginia) for North America; EU (Ireland) for the UK and Europe; US West (Oregon) for Japan and Australia.
   2. Paste the file as `lambda_function.py`.
   3. Set the environment variables `HA_URL`, `HA_TOKEN` and `SKILL_ID`.
   4. Set the timeout to 8 seconds.
   5. Add an **Alexa Skills Kit** trigger restricted to your skill ID.
   6. In the skill's **Endpoint** page, choose **AWS Lambda ARN** and paste the function's ARN.

### D. Try it

9. Open the **Test** tab and set "Skill testing is enabled in" to **Development**. Type or say: `ask home inventory where the hedge trimmer is`.
10. Then try it on your Echo: "Alexa, ask Home Inventory where the hedge trimmer is". You can also say "Alexa, open Home Inventory" and then ask.

### Get the answer on your phone too

So you don't have to ask Alexa to repeat itself, Home Inventory can also send the answer as a notification, including where the thing is, to your phone.

1. Go to Settings → Devices & services → **Home Inventory** → **Configure**.
2. Set **Also send voice answers to your phone** to one of:
   - **Alexa answers only**;
   - **All voice answers**, which also covers Assist and Siri.
3. Choose the **Notification action**. The Home Assistant companion app adds one for each phone, named `mobile_app_<phone name>`.
4. Select **Submit**.

What gets sent:
- Only final answers, such as *"Kink Control Plus Water Hose 50ft is in Plastic tote number 1, located in Shed, Rear right shelving."*
- "Which one did you mean?" questions and "I couldn't find…" answers aren't sent.
- If the notification can't be sent, the spoken answer still works.

The notification goes through Home Assistant's own notify action, so it uses whatever that action reaches: the companion app's push service, or any other notify integration you choose.

### If something goes wrong

The forwarder says what's wrong out loud:

| What Alexa says | What to fix |
|---|---|
| "Home Assistant refused the request…" | The token is wrong, or its user was deleted. |
| "…Alexa support isn't turned on…" | `alexa:` is missing from `configuration.yaml`, or Home Assistant wasn't restarted. |
| "Sorry, I couldn't reach Home Assistant." | `HA_URL` is wrong, or Home Assistant isn't reachable from the internet. |
| "…isn't set up yet…" | `HA_URL` or `HA_TOKEN` is empty. |
| "I couldn't find X in your inventory." | It's working. Try the name as it's written in Home Inventory. Numbers can be said either way ("zero turn" finds "0-turn" and the other way round). |
| "Sorry, I only know where things are" (or "Sorry, I didn't catch that") for "where does the hammer go?" | The skill is older than 0.22.1: update the interaction model and the forwarder code (see *Updating the skill* below). |
| The same, after answering "which one?" | Update the interaction model (it has a `HomeInventoryAnswer` intent for bare replies such as "zero turn") and the forwarder code from 0.19.0. |

### Updating the skill

When a release changes `deploy/alexa/` (0.22.1 for "where does it go?", 0.23.0 for "I've moved…" and yes/no, 0.26.0 for "what's in…"):

1. Developer console → your skill → **Build → Interaction Model → JSON Editor**: paste the new `deploy/alexa/interaction-model.json`, **Save**, **Build skill**.
2. **Code** tab: replace `lambda_function.py` with the new `deploy/alexa/lambda_function.py`, put your `HA_URL`, `HA_TOKEN` and `SKILL_ID` back near the top (Alexa-hosted), **Save**, **Deploy**. With your own AWS Lambda, paste the whole file; the settings stay in its environment variables.

Home Assistant's own "Amazon Alexa Custom Skill" documentation describes the same `/api/alexa` endpoint if you want more background.

A "Smart Home" Alexa skill (including the one Home Assistant Cloud sets up) only controls devices; it can't answer free-text questions, so it doesn't replace the custom skill.
