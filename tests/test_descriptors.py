"""The confirmed descriptors must all exist in the catalogue.

This is the test that turns CLAUDE.md rule 5 into something enforceable: if the
catalogue ever drops one of them, the suite fails instead of the tools quietly
asking BMW for something that no longer exists.
"""

from __future__ import annotations

import pytest

from pitwall_mcp import descriptors as D
from pitwall_mcp.catalogue import ELECTRIC_CATEGORY


@pytest.mark.parametrize("descriptor", D.ALL_CONFIRMED_DESCRIPTORS)
def test_every_confirmed_descriptor_exists(catalogue, descriptor):
    """Each descriptor this project relies on is in the catalogue."""
    assert catalogue.get(descriptor) is not None, (
        f"{descriptor} ya no esta en el catalogo telematico"
    )


def test_catalogue_shape(catalogue):
    """The catalogue is the documented 294 descriptors in 8 categories."""
    assert len(catalogue) == 294
    assert len(catalogue.categories) == 8
    assert catalogue.categories[ELECTRIC_CATEGORY] == 114


def test_container_excludes_the_tyre_diagnosis_descriptor():
    """The diagnosis key has its own endpoint, so it stays out of the container."""
    assert D.TYRE_DIAGNOSIS not in D.CONTAINER_DESCRIPTORS
    assert D.TYRE_DIAGNOSIS in D.ALL_CONFIRMED_DESCRIPTORS
    assert len(D.CONTAINER_DESCRIPTORS) == 32
    assert len(D.ALL_CONFIRMED_DESCRIPTORS) == 33


def test_no_descriptor_is_repeated_in_the_container():
    """A duplicate would waste room in a container of unknown maximum size."""
    assert len(set(D.CONTAINER_DESCRIPTORS)) == len(D.CONTAINER_DESCRIPTORS)


def test_no_electric_descriptor_is_requested(catalogue):
    """This is a petrol vehicle: nothing electric-only may be in the container."""
    electric = [
        d
        for d in D.CONTAINER_DESCRIPTORS
        if (entry := catalogue.get(d)) and entry.category == ELECTRIC_CATEGORY
    ]
    assert electric == []


def test_tyre_descriptors_cover_four_wheels():
    """Four pressures, four targets, four temperatures."""
    assert len(D.TYRE_PRESSURE_DESCRIPTORS) == 4
    assert len(D.TYRE_PRESSURE_TARGET_DESCRIPTORS) == 4
    assert len(D.TYRE_TEMPERATURE_DESCRIPTORS) == 4


@pytest.mark.parametrize("descriptor", D.TYRE_PRESSURE_DESCRIPTORS)
def test_pressures_are_kpa_and_admit_no_measurement(catalogue, descriptor):
    """Pressures come in kPa and `-NA-` is a documented, legitimate value."""
    entry = catalogue.get(descriptor)
    assert entry.unit == "kPa"
    assert D.NO_MEASUREMENT in entry.value_range


def test_software_version_has_no_descriptor(catalogue):
    """Nothing in the catalogue exposes a vehicle software version."""
    suspects = [
        entry
        for entry in catalogue.entries
        if "istep" in entry.technical_descriptor.lower()
        or "softwareversion" in entry.technical_descriptor.lower().replace(".", "")
    ]
    assert suspects == []
