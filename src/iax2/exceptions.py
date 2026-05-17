"""pyiax2 exception hierarchy."""


class IAX2Error(Exception):
    """Base exception for all pyiax2 errors."""


class FrameDecodeError(IAX2Error):
    pass


class AuthenticationError(IAX2Error):
    pass


class CallStateError(IAX2Error):
    pass