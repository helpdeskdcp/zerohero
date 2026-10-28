from app.ws_auth import query_token_ok


def test_equal_tokens_pass_and_everything_else_fails():
    assert query_token_ok("s3cret-token", "s3cret-token")
    assert not query_token_ok("s3cret-token", "s3cret-tokeN") and not query_token_ok("s3cret", "s3cret-token") and not query_token_ok("s3cret-token-x", "s3cret-token")
    assert not query_token_ok(None, "s3cret-token") and not query_token_ok("", "s3cret-token")
    assert not query_token_ok("anything", None) and not query_token_ok("anything", "") and not query_token_ok("", "")     # no configured token -> never authenticated by URL
    assert query_token_ok("tökén-ü", "tökén-ü") and not query_token_ok("tökén-ü", "token-u")                               # non-ASCII does not raise


def test_main_uses_the_helper_for_the_http_gate_and_both_websockets():
    src = open("app/main.py", encoding="utf-8").read()
    assert src.count("_ws_query_token_ok(websocket.query_params.get(\"token\"), API_TOKEN)") == 2 and 'query_params.get("token") == API_TOKEN' not in src
    assert 'token_ok = _ws_query_token_ok(_token_from(request), API_TOKEN)' in src and '== API_TOKEN' not in src
