#!/usr/bin/env python3
"""Deploy a Docker Hub image tag to the open-claw Zeabur service and verify the running version.

usage: zeabur_deploy_image.py <repository> <tag> [--redeploy]

Docker Hub is the only deploy source for the service: this pins the service to an immutable
release tag (updateServiceImageRepo + updateServiceImageTag), waits for the gateway to come back,
and checks that `openclaw --version` inside the running container reports <tag> (catches an image
built from the wrong tree, as happened with 2026.11.6). Env: ZEABUR_TOKEN, ZEABUR_SERVICE_ID,
ZEABUR_ENVIRONMENT_ID. Never prints the token.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

API = "https://api.zeabur.com/graphql"
HEALTH = "https://open-claw.zeabur.app/health"


def gql(query: str, variables: dict) -> dict:
    req = urllib.request.Request(API, json.dumps({"query": query, "variables": variables}).encode(), headers={
        "Authorization": "Bearer " + os.environ["ZEABUR_TOKEN"], "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            body = json.load(r)
    except urllib.error.HTTPError as err:
        body = json.loads(err.read() or b"{}")
    if body.get("errors"):
        raise SystemExit(f"::error::Zeabur API: {body['errors'][0].get('message')}")
    return body.get("data") or {}


def running_version(svc: str, env: str) -> str:
    try:
        data = gql("mutation($s:ObjectID!,$e:ObjectID!){executeCommand(serviceID:$s,environmentID:$e,"
                   "command:[\"openclaw\",\"--version\"]){exitCode output}}", {"s": svc, "e": env})
        return (data.get("executeCommand") or {}).get("output", "").strip()
    except SystemExit:
        return ""


def healthy() -> bool:
    try:
        with urllib.request.urlopen(HEALTH, timeout=10) as r:
            return b'"ok":true' in r.read()
    except Exception:
        return False


def main() -> None:
    repo, tag = sys.argv[1], sys.argv[2]
    svc, env = os.environ["ZEABUR_SERVICE_ID"], os.environ["ZEABUR_ENVIRONMENT_ID"]
    v = {"s": svc, "e": env}
    trig = gql("query($s:ObjectID!,$e:ObjectID!){service(_id:$s){gitTrigger(environmentID:$e){branchName}}}", v)
    if (trig.get("service") or {}).get("gitTrigger"):
        # A git trigger makes Zeabur rebuild main from source on every push, replacing the image.
        gql("mutation($s:ObjectID!,$e:ObjectID!){updateGitTrigger(serviceID:$s,environmentID:$e,trigger:null)}", v)
        print("removed the Zeabur git trigger (Docker Hub is the deploy source)")
    gql("mutation($s:ObjectID!,$e:ObjectID!,$r:String!,$t:String!){a:updateServiceImageRepo(serviceID:$s,"
        "environmentID:$e,repository:$r) b:updateServiceImageTag(serviceID:$s,environmentID:$e,tag:$t)}",
        dict(v, r=repo, t=tag))
    if "--redeploy" in sys.argv:
        gql("mutation($s:ObjectID!,$e:ObjectID!){redeployService(serviceID:$s,environmentID:$e)}", v)
    print(f"Zeabur service pinned to {repo}:{tag}")
    for attempt in range(1, 41):
        time.sleep(15)
        ver = running_version(svc, env)
        ok = healthy()
        print(f"attempt {attempt}/40: healthy={ok} running={ver or '?'}")
        if ok and f" {tag} " in f" {ver} ":
            print(f"verified: gateway healthy and running {ver}")
            return
    raise SystemExit(f"::error::{repo}:{tag} did not become healthy with the expected version within 10 minutes")


if __name__ == "__main__":
    main()
