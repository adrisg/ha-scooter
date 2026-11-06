"""Make the harness find this repository's integration.

pytest-homeassistant-custom-component imports its own testing_config/custom_components
package; add ours to that package's search path.
"""
import os

import custom_components

OURS = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "custom_components"))
if OURS not in list(custom_components.__path__):
    custom_components.__path__.insert(0, OURS)
