# Security

## Reporting a vulnerability

Please report vulnerabilities privately, through GitHub's **private
vulnerability reporting** for this repository: the *Security* tab, then
*Report a vulnerability*. That keeps the report private until a fix
is ready.

If that is unavailable to you, email **rob@robknowles.com** instead.

Please include what you found, how to reproduce it, the version or commit,
and what an attacker could do with it. Do not include anyone's real
continuing-education records or credentials; a fictional reproduction is
enough.

You should get an acknowledgement within a few days. Please allow
reasonable time for a fix before any public disclosure; we will agree a
date with you.

## What is in scope

Registrar is designed to run on `127.0.0.1` (the default) or on one private
network interface the operator already trusts. It has **no authentication
by design**: the network boundary is the access control. "An attacker on a
network the operator exposed it to can read the ledger" is therefore
expected behaviour, not a vulnerability. Things that are in scope include:

- a bind default other than loopback, or anything that silently widens it;
- bypassing the anti-forgery (CSRF) check on any POST;
- reaching the web app through a Host name it does not answer to (for
  example by DNS rebinding);
- script execution in the web UI (it ships no JavaScript and its CSP
  allows none);
- path traversal or content-type confusion when serving evidence files;
- the evidence integrity check (sha256 at upload, re-checked on serve and
  when building a bundle) passing a file that was altered;
- the Telegram capture accepting messages from a chat other than the one
  configured (or from any chat when none is configured), or logging the bot
  token;
- anything that would store or transmit a certifying body's portal login.
  Registrar never asks for one; if you find a path where it could, that is
  a vulnerability.

## Supported versions

Only the latest commit on the default branch is supported during the early
release.
