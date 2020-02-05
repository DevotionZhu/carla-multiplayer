from typing import Callable, Dict, Tuple, Optional
from typing import NamedTuple

import pygame

from .controller import ControllerState, handle_steer_deadzone


class RawControllerState(NamedTuple):
    axis_data: Dict[int, Optional[float]] = {}
    button_data: Dict[int, bool] = {}
    hat_data: Dict[int, Tuple[int, int]] = {}


class ControllerEventHandler(object):
    def __init__(self, controller_index: int, callback: Callable):
        self._controller_index: int = controller_index
        self._callback: Callable = callback

        self._controller: pygame.joystick.JoystickType = pygame.joystick.Joystick(self._controller_index)
        self._controller.init()

        self._axis_data: Dict[int, Optional[float]] = {
            i: None for i in range(0, self._controller.get_numaxes())
        }

        self._button_data: Dict[int, bool] = {
            i: False for i in range(0, self._controller.get_numbuttons())
        }

        self._hat_data: Dict[int, Tuple[int, int]] = {
            i: (0, 0) for i in range(0, self._controller.get_numhats())
        }

    def handle_event(self, event: pygame.event.EventType):
        if event.type == pygame.JOYAXISMOTION:
            self._axis_data[event.axis] = round(event.value, 2)
        elif event.type == pygame.JOYBUTTONDOWN:
            self._button_data[event.button] = True
        elif event.type == pygame.JOYBUTTONUP:
            self._button_data[event.button] = False
        elif event.type == pygame.JOYHATMOTION:
            self._hat_data[event.hat] = event.value
        else:
            return

        if not callable(self._callback):
            raise TypeError('expected {}} to be callable but was {}'.format(
                repr(self._callback),
                type(self._callback)
            ))

        self._callback(
            RawControllerState(
                axis_data=self._axis_data,
                button_data=self._button_data,
                hat_data=self._hat_data
            )
        )


class GamepadController(object):
    def __init__(self, controller_index: int, callback: Callable):
        self._controller_index: int = controller_index
        self._callback: Callable = callback

        self._handler: ControllerEventHandler = ControllerEventHandler(
            controller_index=self._controller_index,
            callback=self._callback_wrapper
        )

        self._reverse: bool = False

        self._last_controller_state: Optional[ControllerState] = None

    def _callback_wrapper(self, raw_controller_state: RawControllerState):
        controller_state = self._handle_callback(raw_controller_state)
        if controller_state == self._last_controller_state:
            return

        self._callback(controller_state)

        self._last_controller_state = controller_state

    def _handle_callback(self, controller_state: RawControllerState):
        if controller_state.axis_data[5] is not None:
            throttle = round((controller_state.axis_data[5] + 1.0) / 2.0, 2)
        else:
            throttle = 0.0

        if controller_state.axis_data[4] is not None:
            brake = round((controller_state.axis_data[4] + 1.0) / 2.0, 2)
        else:
            brake = 0.0

        if controller_state.axis_data[0] is not None:
            steer = round(controller_state.axis_data[0], 2)
        else:
            steer = 0.0

        hand_brake = controller_state.button_data[0]

        select_forward = controller_state.button_data[11] is True
        select_reverse = controller_state.button_data[12] is True

        if select_forward:
            self._reverse = False
        elif select_reverse:
            self._reverse = True

        reset = controller_state.button_data[6]

        if not callable(self._callback):
            raise TypeError('expected {}} to be callable but was {}'.format(
                repr(self._callback),
                type(self._callback)
            ))

        return ControllerState(
            throttle=throttle,
            brake=brake,
            steer=handle_steer_deadzone(steer),
            hand_brake=hand_brake,
            reverse=self._reverse,
            reset=reset,
        )

    def handle_event(self, event: pygame.event.EventType):
        self._handler.handle_event(event)


if __name__ == '__main__':
    def _callback(controller_state: ControllerState):
        print(controller_state)

        return


    pygame.init()
    pygame.joystick.init()
    pygame.display.set_mode()

    _controller = GamepadController(
        controller_index=0,
        callback=_callback
    )

    _clock = pygame.time.Clock()

    _stopped = False
    while not _stopped:
        try:
            for e in pygame.event.get():
                if e.type == pygame.QUIT:
                    _stopped = True
                    break

                _controller.handle_event(e)

            _clock.tick(24)
        except KeyboardInterrupt:
            break

    pygame.quit()