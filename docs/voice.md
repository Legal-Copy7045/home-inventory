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

Create `custom_sentences/en/home_inventory.yaml` in your Home Assistant configuration folder (next to `configuration.yaml`):

```yaml
language: "en"
intents:
  HomeInventoryWhereIs:
    data:
      - sentences:
          - "where is [the|my] {item}"
          - "where are [the|my] {item}"
          - "where did I (put|leave) [the|my] {item}"
          - "find [the|my] {item}"
lists:
  item:
    wildcard: true
```

Restart Home Assistant (or reload conversation). Then say or type "Where is the hedge trimmer?" to Assist.

Other languages: put a file with the same intent name in `custom_sentences/<language>/`, with sentences in that language.

If you use an AI conversation agent (OpenAI, Gemini…) with Assist, you can also expose the `home_inventory.get_item_location` action through a script (see `home-assistant.md`) so the agent can look things up.

## 2. Siri

Siri can't call Home Assistant intents by itself, but it can run an Apple Shortcut that sends text to Assist using the Home Assistant companion app:

1. In the **Shortcuts** app, create a shortcut named **"Where is"**.
2. Add **Dictate Text** (or **Ask for Input**).
3. Add the Home Assistant app's **Assist** action and pass it the text `where is` followed by the dictated text.
4. Add **Speak Text** with the Assist response (if your companion-app version doesn't speak it already).

Then: "Hey Siri, Where is" → "hedge trimmer" → Siri answers. Siri needs the two-step exchange because shortcuts can't take a free-text parameter in the same phrase.

## 3. Alexa custom skill

"Alexa, ask Home Inventory where the hedge trimmer is" needs your own (private, development-mode) Alexa custom skill. Home Assistant's Alexa integration passes custom-skill intents to the Home Assistant intent with the same name and turns each slot into an intent slot, so no `intent_script` is needed (checked against Home Assistant 2026.2's source, and covered by the integration tests).

**Before you start, know the trade-off:** Amazon's servers must be able to call your Home Assistant over HTTPS (your own domain with a valid certificate, or another public HTTPS route to it). The Home Inventory App stays private either way; only Home Assistant is reached.

1. In `configuration.yaml`, enable Alexa custom-skill support:

   ```yaml
   alexa:
   ```

2. In the Alexa developer console, create a **Custom** skill with invocation name **home inventory**, and an intent named exactly **`HomeInventoryWhereIs`** with one slot named **`item`** of type **`AMAZON.SearchQuery`**. Sample utterances:

   ```
   where {item} is
   where the {item} is
   where my {item} is
   where are my {item}
   to find {item}
   ```

3. Point the skill's endpoint at Home Assistant's Alexa endpoint, **`https://<your Home Assistant>/api/alexa`**. That endpoint requires Home Assistant authentication, so follow Home Assistant's "Amazon Alexa Custom Skill" documentation for how to authenticate the skill (it changes between releases, so use the current page rather than a copy here).

4. Test in the developer console's **Test** tab: "ask home inventory where the hedge trimmer is".

A "Smart Home" Alexa skill (including the one Home Assistant Cloud sets up) only controls devices; it can't answer free-text questions, so it doesn't replace the custom skill.
