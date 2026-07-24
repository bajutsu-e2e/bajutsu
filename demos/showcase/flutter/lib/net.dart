import 'dart:convert';
import 'dart:io';

/// The showcase's network calls (SPEC §6). Each call mirrors its outcome to a `*.status` value
/// (`loading` → `done`/`error`) so a scenario can wait on the response before asserting. Any HTTP
/// response counts as `done`; a transport failure, or the total deadline elapsing, is `error`.
///
/// A request deliberately carries a secret header (`Authorization: Bearer …`) and, on the POST, a
/// `password` body field so redaction has something to mask (SPEC §6 / DESIGN §9).
///
/// Unlike the iOS (URLSession + BajutsuKit) and Android (OkHttp + BajutsuAndroid) twins, these use
/// Dart's own `HttpClient`, which does not flow through the native interceptors, so `network`
/// capture and `mocks` do not observe Flutter traffic (BE-0008 out-of-scope note in drivers.md).
/// The status mirror still drives the deterministic wait/assert the shared scenarios rely on.
class Net {
  // A total deadline over the whole exchange, not just the TCP connect: an endpoint that accepts the
  // connection and then never responds must still resolve to `error`, matching the URLSession /
  // OkHttp request timeouts the native twins use, rather than parking the status on `loading`.
  static const Duration _deadline = Duration(seconds: 15);

  static final HttpClient _client = HttpClient()..connectionTimeout = _deadline;

  /// GET a URL, returning `done` on any response, `error` on a transport failure or timeout.
  static Future<String> get(String url) => _run(() async {
        final request = await _client.getUrl(Uri.parse(url));
        request.headers.set(HttpHeaders.authorizationHeader, 'Bearer demo-secret-abc123');
        final response = await request.close();
        await response.drain<void>();
      });

  /// POST the training-log entry to `<base>/post` as JSON.
  static Future<String> postLog(String base, String note, int count, bool intense) => _run(() async {
        final request = await _client.postUrl(Uri.parse('$base/post'));
        request.headers.set(HttpHeaders.authorizationHeader, 'Bearer demo-secret-abc123');
        request.headers.contentType = ContentType.json;
        request.write(jsonEncode({'note': note, 'count': count, 'intense': intense, 'password': 'hunter2'}));
        final response = await request.close();
        await response.drain<void>();
      });

  static Future<String> _run(Future<void> Function() call) async {
    try {
      await call().timeout(_deadline);
      return 'done';
    } catch (_) {
      return 'error';
    }
  }
}
