# Webhook, a TapFlow Plugin

Send an HTTP request from a [TapFlow](https://github.com/E-n-N-D/TapFlow) Tile or Workflow step: trigger a home-automation scene, ping a CI job, post to a chat webhook, or call any local service.

The Plugin adds one Action Kind, **Send Webhook** (`tapflow.webhook.send`), to the TapFlow Desktop Agent. It's written with the [`tapflow_plugin`](https://github.com/E-n-N-D/TapFlow/blob/main/desktop/plugin_sdk/README.md) SDK and uses only Python's standard library, so it's also a small, complete example of a Plugin: a manifest, one `@action`, tests, and a CI workflow that packs and publishes it.

## Install it

1. Download `tapflow.webhook-<version>.tapflow-plugin` from the [latest release](https://github.com/E-n-N-D/tapflow-plugin-webhook/releases/latest).
2. On the Windows PC running TapFlow, open the TapFlow window's **Plugins** page, click **Install from file** and pick the file. Or, in a terminal:

   ```
   tapflow plugin install tapflow.webhook-1.0.0.tapflow-plugin
   ```

3. TapFlow shows the Plugin's name, version and author, and asks before installing it. A Plugin can do anything you can on your computer, so install only Plugins you trust.

**Send Webhook** then appears in the Action picker on every connected phone, under **Webhook**. The phone needs the **Plugins** Permission to run it. Installing a newer version later updates it in place.

The Plugin runs on Windows only, like TapFlow's Desktop Agent.

## Params

| Param | Type | Default | What it does |
|---|---|---|---|
| **URL** (`url`) | text, required | | The `http://` or `https://` address to send to. Spaces and other characters a URL can't hold are percent-encoded. It can't hold a user name or password. |
| **Method** (`method`) | GET, POST, PUT, PATCH or DELETE | POST | The HTTP method. |
| **Body** (`body`) | multi-line text | empty | What to send, encoded as UTF-8. Empty sends no body. |
| **Content type** (`content_type`) | text | `application/json` | The `Content-Type` header sent with a body. No `Content-Type` is sent without a body, or when this is empty. |
| **Fail on an error status** (`fail_on_error`) | switch | on | When on, an answer that isn't 2xx ends the run Failed with its status. When off, any answer ends it Done. |
| **Timeout (seconds)** (`timeout_s`) | number, 1 to 300 | 30 | How long the whole run may take, from looking up the host to the end of the answer's headers. |

## How a run ends

| What happens | The run on the phone |
|---|---|
| The server answers 2xx | Done |
| The server answers anything else, a redirect included, with **Fail on an error status** on | Failed: `The server answered 404 Not Found` |
| The same, with it off | Done |
| The URL isn't `http://` or `https://`, holds a user name or password, or its port isn't a number | Failed: `The URL must be an http:// or https:// address`, `The URL can't hold a user name or password` or `The URL isn't valid` |
| The content type has a character a header can't hold, such as a non-Latin-1 one | Failed: `The content type isn't valid` |
| The host can't be reached, such as an unknown name, a refused connection or an untrusted HTTPS certificate | Failed: `Couldn't connect to example.com: …` |
| The run takes longer than the timeout | Failed: `example.com didn't answer within 30 seconds` |
| The server hangs up without answering | Failed: `example.com didn't answer: …` |
| You cancel it, press Escape, or the phone that started it goes away | Cancelled, at once |

**An HTTPS URL that fails with `certificate verify failed`.** The Plugin checks HTTPS certificates against the root certificates in Windows' own store. Windows doesn't keep every trusted root there: it adds one the first time a Windows app that uses its HTTPS, such as Edge, needs it. Until then, a server whose certificate chains to a root that Windows hasn't added fails with `Couldn't connect to example.com: [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get local issuer certificate`. To fix it, open the URL once in Edge, then run the Tile again.

A cancel heard before the connection to the server is up means the request is never sent. A request already sent can't be taken back, so a cancel after that may still leave the server with it.

Redirects aren't followed: a webhook's method and body would be lost or changed on the way, so a redirect ends the run as any other non-2xx status does. No proxy is used, not even the system's. Requests name the Plugin as their `User-Agent`, such as `TapFlow-Webhook/1.0.0`. The agent's log (`~/.tapflow/logs/agent.log`) records each request's method, host and status, but not the full URL, since a webhook's URL often holds its secret. The message the phone shows names only the host too.

## Develop it

The Plugin itself is the [`webhook/`](webhook) folder: its manifest, [`tapflow-plugin.toml`](webhook/tapflow-plugin.toml), and its entry module, [`tapflow_webhook.py`](webhook/tapflow_webhook.py). Everything else is tooling.

Run the tests with Python 3.11, the Desktop Agent's Python, and the SDK:

```
pip install tapflow-plugin pytest
pytest
```

The tests run `send` with the SDK's test helper, `tapflow_plugin.testing.run_action`, against a real HTTP server on `127.0.0.1`.

Pack the Plugin on a Windows PC with TapFlow installed:

```
tapflow plugin pack webhook
```

It checks the Plugin and writes `tapflow.webhook-<version>.tapflow-plugin`. See [Write a TapFlow Plugin](https://github.com/E-n-N-D/TapFlow/blob/main/docs/guides/writing-a-plugin.md) for the SDK, the manifest and packing.

### CI and releases

[`ci.yml`](.github/workflows/ci.yml) runs on every push and pull request, on Windows and Python 3.11. It lints and type-checks, runs the tests, packs the Plugin with `tapflow plugin pack`, and uploads the file as a build artifact. It takes the SDK and the pack command from TapFlow's source at a pinned commit (`TAPFLOW_REF`), checked out with the read-only `TAPFLOW_REPO_TOKEN` secret.

To release a version:

1. Set `version` in `webhook/tapflow-plugin.toml`, such as `1.1.0`, and merge it to `main`.
2. Push a tag naming that version: `git tag v1.1.0 && git push origin v1.1.0`.

CI then creates the GitHub release `v1.1.0` with the packed file attached. It refuses a tag that isn't `v` followed by the manifest's version.

A change to `send`'s params that a Tile saved with an older version can't satisfy, such as a new required param, needs a new kind name: see the rename rule in [Write a TapFlow Plugin](https://github.com/E-n-N-D/TapFlow/blob/main/docs/guides/writing-a-plugin.md#updating-a-plugin).

## License

[MIT](LICENSE).
