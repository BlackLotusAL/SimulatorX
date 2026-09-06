from sut_integration.reference_sut.detector import DetectorSUT


def create_sut(descriptor):
    return DetectorSUT(descriptor["endpoint"], timeout=4)


def start_manual(sut, descriptor):
    return sut.start_check()
