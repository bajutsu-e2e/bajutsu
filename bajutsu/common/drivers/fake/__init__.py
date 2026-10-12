"""In-memory fake driver implementing the Driver Protocol.

Lets the orchestrator (the Tier2 runner) be tested without a Simulator. The
`react` callback scripts "the screen changes in response to an action".
"""

from .fake_driver import _UNIT as _UNIT
from .fake_driver import FakeDriver, React
from .fake_network_collector import FakeNetworkCollector
from .resolving_fake_driver import ResolvingFakeDriver

__all__ = ["FakeDriver", "FakeNetworkCollector", "React", "ResolvingFakeDriver"]
