import base64
import copy
import json
from typing import Any

import pytest
from conftest import Services
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from bgl import rekor
from bgl.errors import WitnessError

KEY = Ed25519PrivateKey.from_private_bytes(bytes(32))
PUBLIC = KEY.public_key().public_bytes_raw()
HEAD = "ab" * 32


def test_witness_message_and_digest() -> None:
    assert rekor.witness_message(HEAD) == b"bgl-witness-v1:" + HEAD.encode()
    with pytest.raises(ValueError):
        rekor.witness_message("AB" * 32)


def test_entry_shape() -> None:
    entry = rekor.build_entry(KEY, HEAD)
    assert (entry["kind"], entry["apiVersion"]) == ("rekord", "0.0.1")
    signature = entry["spec"]["signature"]
    assert signature["format"] == "ssh"
    assert base64.b64decode(entry["spec"]["data"]["content"]) == (
        rekor.witness_message(HEAD)
    )


class TestSubmitAndCheck:
    def record(self, services: Services) -> dict[str, Any]:
        return rekor.submit(services.REKOR, KEY, HEAD)

    def test_record_checks_out(self, services: Services) -> None:
        record = self.record(services)
        facts = rekor.check_record(record, PUBLIC)
        assert facts.log_index == 5000
        assert facts.data_hash == rekor.message_digest(HEAD)
        assert record["head"] == HEAD
        assert record["rekor_url"] == services.REKOR

    def test_record_survives_json_round_trip(self, services: Services) -> None:
        record = json.loads(json.dumps(self.record(services)))
        rekor.check_record(record, PUBLIC)

    def test_later_entries_still_verify(self, services: Services) -> None:
        records = [rekor.submit(services.REKOR, KEY, f"{i:064x}") for i in range(5)]
        for record in records:
            rekor.check_record(record, PUBLIC)

    @pytest.mark.parametrize(
        ("change", "message"),
        [
            ("head", "different head"),
            ("uuid", "UUID"),
            ("log_id", "log ID"),
            ("log_key", "log ID"),
            ("set", "signed entry timestamp"),
            ("time", "signed entry timestamp"),
            ("proof_hash", "inclusion proof"),
            ("root", "inclusion proof"),
            ("checkpoint_sig", "checkpoint signature"),
            ("checkpoint_size", "checkpoint does not match"),
            ("other_writer", "different public key"),
            ("format", "format version"),
        ],
    )
    def test_tampering_is_caught(
        self, services: Services, change: str, message: str
    ) -> None:
        record = self.record(services)
        rekor.submit(services.REKOR, KEY, "cd" * 32)  # so the proof has hashes
        record = rekor.submit(services.REKOR, KEY, "ef" * 32)
        bad = copy.deepcopy(record)
        proof = bad["entry"]["verification"]["inclusionProof"]
        if change == "head":
            bad["head"] = "cd" * 32
        elif change == "uuid":
            bad["uuid"] = bad["uuid"][:-1] + ("0" if bad["uuid"][-1] != "0" else "1")
        elif change == "log_id":
            bad["entry"]["logID"] = "00" * 32
        elif change == "log_key":
            from fakes import FakeRekor

            other = FakeRekor()
            bad["log_public_key"] = other.pem.decode()
            other.stop()
        elif change == "set":
            bad["entry"]["verification"]["signedEntryTimestamp"] = base64.b64encode(
                b"0" * 70
            ).decode()
        elif change == "time":
            bad["entry"]["integratedTime"] += 1
        elif change == "proof_hash":
            proof["hashes"][0] = "00" * 32
        elif change == "root":
            proof["rootHash"] = "00" * 32
        elif change == "checkpoint_sig":
            lines = proof["checkpoint"].split("\n")
            name, signature = lines[4].rsplit(" ", 1)
            raw = bytearray(base64.b64decode(signature))
            raw[-1] ^= 1
            lines[4] = name + " " + base64.b64encode(bytes(raw)).decode()
            proof["checkpoint"] = "\n".join(lines)
        elif change == "checkpoint_size":
            lines = proof["checkpoint"].split("\n")
            lines[1] = str(int(lines[1]) + 1)
            proof["checkpoint"] = "\n".join(lines)
        elif change == "other_writer":
            other = Ed25519PrivateKey.generate().public_key().public_bytes_raw()
            with pytest.raises(WitnessError, match=message):
                rekor.check_record(bad, other)
            return
        elif change == "format":
            bad["format_version"] = 2
        with pytest.raises(WitnessError, match=message):
            rekor.check_record(bad, PUBLIC)


class TestService:
    def test_search_and_retrieve(self, services: Services) -> None:
        first = rekor.submit(services.REKOR, KEY, HEAD)
        second = rekor.submit(services.REKOR, KEY, "cd" * 32)
        other = Ed25519PrivateKey.generate()
        rekor.submit(services.REKOR, other, "ef" * 32)
        found = rekor.search_by_key(services.REKOR, PUBLIC)
        assert found == sorted([first["uuid"], second["uuid"]])
        entries = rekor.retrieve(services.REKOR, found)
        assert set(entries) == set(found)
        for uuid in found:
            rekor.check_entry(uuid, entries[uuid], PUBLIC, services.rekor.pem.decode())

    def test_retrieve_in_batches(self, services: Services) -> None:
        uuids = [
            rekor.submit(services.REKOR, KEY, f"{i:064x}")["uuid"] for i in range(23)
        ]
        assert set(rekor.retrieve(services.REKOR, uuids)) == set(uuids)

    def test_unreachable(self, services: Services) -> None:
        services.rekor.down = True
        with pytest.raises(WitnessError, match="503"):
            rekor.submit(services.REKOR, KEY, HEAD)

    def test_search_unavailable(self, services: Services) -> None:
        services.rekor.search_works = False
        with pytest.raises(WitnessError, match="404"):
            rekor.search_by_key(services.REKOR, PUBLIC)


def test_checkpoint_parsing() -> None:
    signature = base64.b64encode(b"\x00\x01\x02\x03sig").decode()
    text = f"origin - 1\n7\n{base64.b64encode(b'r' * 32).decode()}\n\n— o {signature}\n"
    origin, size, root, signatures, signed = rekor.parse_checkpoint(text)
    assert (origin, size, root) == ("origin - 1", 7, b"r" * 32)
    assert signatures == [("o", b"\x00\x01\x02\x03sig")]
    assert signed == text.split("\n\n")[0].encode() + b"\n"
    for bad in ("no blank line", "a\n\n— o x\n", "o\nx\nr\n\n— o AAAA\n"):
        with pytest.raises(WitnessError):
            rekor.parse_checkpoint(bad)


def test_dishonest_log_accepting_a_bad_signature_is_caught(
    services: Services,
) -> None:
    """BGL checks the SSH signature itself instead of trusting the log."""
    services.rekor.check_signatures = False
    message = rekor.witness_message(HEAD)
    forged = rekor.build_entry(KEY, "cd" * 32)
    forged["spec"]["data"]["content"] = base64.b64encode(message).decode()
    status, _, data = services.rekor._create(forged)
    assert status == 201
    uuid, entry = next(iter(json.loads(data).items()))
    record = rekor.make_record(
        services.REKOR, HEAD, uuid, entry, services.rekor.pem.decode()
    )
    with pytest.raises(WitnessError, match="SSH signature"):
        rekor.check_record(record, PUBLIC)
