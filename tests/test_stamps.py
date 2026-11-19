import pytest
from conftest import Services
from opentimestamps.core.notary import BitcoinBlockHeaderAttestation
from opentimestamps.core.op import OpAppend, OpSHA256
from opentimestamps.core.timestamp import DetachedTimestampFile, Timestamp

from bgl import stamps
from bgl.errors import WitnessError

HEAD = "ab" * 32


class TestStamp:
    def test_both_calendars_pending(self, services: Services) -> None:
        proof, failed = stamps.stamp(HEAD, services.CALENDARS)
        assert failed == []
        state = stamps.state(proof, HEAD)
        assert state.pending == services.CALENDARS
        assert state.heights == ()

    def test_one_calendar_down_is_reported(self, services: Services) -> None:
        services.calendars[1].down = True
        proof, failed = stamps.stamp(HEAD, services.CALENDARS)
        assert failed == [services.CALENDARS[1]]
        assert stamps.state(proof, HEAD).pending == (services.CALENDARS[0],)

    def test_all_down_raises(self, services: Services) -> None:
        for calendar in services.calendars:
            calendar.down = True
        with pytest.raises(WitnessError, match="no OpenTimestamps calendar"):
            stamps.stamp(HEAD, services.CALENDARS)

    def test_proof_is_standard_ots_for_the_witness_message(
        self, services: Services
    ) -> None:
        proof, _ = stamps.stamp(HEAD, services.CALENDARS)
        assert proof.startswith(b"\x00OpenTimestamps\x00\x00Proof\x00")
        assert stamps.read(proof, HEAD).file_digest == stamps.digest(HEAD)

    def test_proof_for_another_head_is_refused(self, services: Services) -> None:
        proof, _ = stamps.stamp(HEAD, services.CALENDARS)
        with pytest.raises(WitnessError, match="different head"):
            stamps.read(proof, "cd" * 32)

    @pytest.mark.parametrize("data", [b"", b"junk", b"\x00OpenTimestamps\x00"])
    def test_garbage_is_refused(self, data: bytes) -> None:
        with pytest.raises(WitnessError, match="not a valid"):
            stamps.read(data, HEAD)


class TestUpgradeAndConfirm:
    def test_pending_until_bitcoin_confirms(self, services: Services) -> None:
        proof, _ = stamps.stamp(HEAD, services.CALENDARS)
        upgraded, changed = stamps.upgrade(proof, HEAD, services.CALENDARS)
        assert not changed
        assert upgraded == proof

    def test_upgrade_then_confirm_against_explorers(self, services: Services) -> None:
        proof, _ = stamps.stamp(HEAD, services.CALENDARS)
        services.confirm_all()
        upgraded, changed = stamps.upgrade(proof, HEAD, services.CALENDARS)
        assert changed
        assert stamps.state(upgraded, HEAD).heights
        source = stamps.explorer_source(services.EXPLORERS)
        confirmations = stamps.confirm(upgraded, HEAD, source)
        assert confirmations
        assert confirmations[0].height >= 850000

    def test_only_configured_calendars_are_asked(self, services: Services) -> None:
        proof, _ = stamps.stamp(HEAD, services.CALENDARS)
        services.confirm_all()
        _, changed = stamps.upgrade(proof, HEAD, ["https://elsewhere.example"])
        assert not changed

    def test_explorers_that_disagree(self, services: Services) -> None:
        proof, _ = stamps.stamp(HEAD, services.CALENDARS)
        services.confirm_all()
        upgraded, _ = stamps.upgrade(proof, HEAD, services.CALENDARS)
        services.explorers[1].lie = True
        with pytest.raises(WitnessError, match="disagree"):
            stamps.confirm(upgraded, HEAD, stamps.explorer_source(services.EXPLORERS))

    def test_one_explorer_is_not_enough(self) -> None:
        with pytest.raises(ValueError, match="two"):
            stamps.explorer_source(["https://explorer-a.example/api"])

    def test_wrong_block_is_an_error(self) -> None:
        digest = stamps.digest(HEAD)
        timestamp = Timestamp(digest)
        root = timestamp.ops.add(OpAppend(b"\x01")).ops.add(OpSHA256())
        root.attestations.add(BitcoinBlockHeaderAttestation(7))
        proof = stamps._to_bytes(DetachedTimestampFile(OpSHA256(), timestamp))

        def source(height: int) -> tuple[str, int]:
            return "00" * 32, 1

        with pytest.raises(WitnessError, match="does not reach"):
            stamps.confirm(proof, HEAD, source)

    def test_matching_block(self) -> None:
        digest = stamps.digest(HEAD)
        timestamp = Timestamp(digest)
        root = timestamp.ops.add(OpAppend(b"\x01")).ops.add(OpSHA256())
        root.attestations.add(BitcoinBlockHeaderAttestation(7))
        proof = stamps._to_bytes(DetachedTimestampFile(OpSHA256(), timestamp))

        def source(height: int) -> tuple[str, int]:
            return root.msg[::-1].hex(), 1600000000

        assert stamps.confirm(proof, HEAD, source)[0].block_time == 1600000000
