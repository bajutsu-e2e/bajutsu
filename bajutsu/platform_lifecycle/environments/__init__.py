"""One module per concrete `Environment` implementer (iOS, Android, web, XCUITest, fake).

The package root (`__init__.py`) re-exports these classes and the `environment_for` factory; import
from there rather than reaching into a specific platform module.
"""
