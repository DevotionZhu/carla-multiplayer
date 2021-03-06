from io import BytesIO
from queue import Queue, Full, Empty
from threading import Thread
from typing import Optional

import numpy
from PIL import Image

from .threader import Threader
from .udp import Sender

try:  # cater for python3 -m (module) vs python3 (file)
    from . import wrapped_carla as carla
except ImportError:
    import wrapped_carla as carla

_FPS = 30
_WIDTH = 640
_HEIGHT = 360
_SENSOR_BLUEPRINT_NAME = 'sensor.camera.rgb'
_SENSOR_TRANSFORM = carla.Transform(
    carla.Location(-15, 0, 15),
    carla.Rotation(16.875, 0, 0)
)
_CARLA_PORT = 2000
_CARLA_TIMEOUT = 2.0
_QUEUE_SIZE = 2


def create_sensor(
        client: carla.Client,
        actor_id: int,
        sensor_blueprint_name: str = _SENSOR_BLUEPRINT_NAME,
        fps: int = _FPS,
        width: int = _WIDTH,
        height: int = _HEIGHT,
        transform: carla.Transform = _SENSOR_TRANSFORM) -> carla.ServerSideSensor:
    world = client.get_world()
    world.wait_for_tick()

    actor = world.get_actor(actor_id)
    world.wait_for_tick()

    blueprint_library = world.get_blueprint_library()
    sensor_blueprint = blueprint_library.find(sensor_blueprint_name)
    sensor_blueprint.set_attribute('sensor_tick', str(1.0 / fps))
    sensor_blueprint.set_attribute('image_size_x', str(width))
    sensor_blueprint.set_attribute('image_size_y', str(height))
    sensor = world.spawn_actor(
        sensor_blueprint,
        transform,
        attach_to=actor,
        attachment_type=carla.AttachmentType.SpringArm
    )
    world.wait_for_tick()

    return sensor


def get_sensor(client: carla.Client, actor_id: int) -> carla.ServerSideSensor:
    world = client.get_world()
    world.wait_for_tick()

    sensor = world.get_actor(actor_id)
    world.wait_for_tick()

    if sensor is None:
        raise ValueError('failed to get sensor for actor_id {}; valid options right now are {}'.format(
            actor_id,
            {x.id: x.type_id for x in world.get_actors().filter('sensor.*')}
        ))

    return sensor


def delete_sensor(client: carla.Client, actor_id: int):
    get_sensor(client, actor_id).destroy()
    client.get_world().wait_for_tick()


def _carla_image_to_bgra_array(image: carla.Image):
    array = numpy.frombuffer(image.raw_data, dtype=numpy.dtype("uint8"))
    array = numpy.reshape(array, (image.height, image.width, 4))

    return array


def _carla_image_to_rgb_array(image: carla.Image):
    array = _carla_image_to_bgra_array(image)
    array = array[:, :, :3]
    array = array[:, :, ::-1]

    return array


def _carla_image_to_webp_bytes(image: carla.Image):
    rgb_array = _carla_image_to_rgb_array(image)
    pil_image = Image.fromarray(rgb_array)
    buffer = BytesIO()
    pil_image.save(buffer, format='webp')

    return buffer.getvalue()


class Sensor(Threader):
    def __init__(self, client: carla.Client, actor_id: int, queue_size: int, sender: Sender, host: str, port: int):
        super().__init__()

        self._client: carla.Client = client
        self._actor_id: int = actor_id
        self._queue_size: int = queue_size
        self._sender: Sender = sender
        self._host: str = host
        self._port: int = port

        self._sensor: Optional[carla.ServerSideSensor] = None
        self._carla_images: Queue = Queue(maxsize=self._queue_size)
        self._webp_bytes: Queue = Queue(maxsize=self._queue_size)

    def _add_image_to_carla_images_queue(self, image: carla.Image):
        while not self._stop_event.is_set():
            try:
                self._carla_images.put_nowait(image)
                break
            except Full:
                try:
                    self._carla_images.get_nowait()
                except Empty:
                    pass

    def _fill_webp_bytes_queue_from_carla_images_queue(self):
        while not self._stop_event.is_set():
            try:
                carla_image = self._carla_images.get(timeout=1)
            except Empty:
                continue

            webp_bytes = _carla_image_to_webp_bytes(carla_image)

            while not self._stop_event.is_set():
                try:
                    self._webp_bytes.put_nowait(webp_bytes)
                    break
                except Full:
                    try:
                        self._webp_bytes.get_nowait()
                    except Empty:
                        pass

    def _send_datagrams_from_webp_bytes_queue(self):
        while not self._stop_event.is_set():
            try:
                webp_bytes = self._webp_bytes.get(timeout=1)
            except Empty:
                continue

            try:
                self._sender.send_datagram(webp_bytes, (self._host, self._port))
            except Full:
                pass

    def _create_threads(self):
        self._threads = [
            Thread(target=self._fill_webp_bytes_queue_from_carla_images_queue),
            Thread(target=self._send_datagrams_from_webp_bytes_queue)
        ]

    def _before_start(self):
        self._sensor = get_sensor(self._client, self._actor_id)
        self._sensor.listen(self._add_image_to_carla_images_queue)

    def _after_stop(self):
        self._sensor.stop()


if __name__ == '__main__':
    import argparse
    import time

    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--client-host', type=str, required=True)
    parser.add_argument('--carla-host', type=str, required=True)
    parser.add_argument('--actor-id', type=int, required=True)
    parser.add_argument('--sensor-blueprint_name', type=str, default=_SENSOR_BLUEPRINT_NAME)
    parser.add_argument('--carla-port', type=int, default=_CARLA_PORT)
    parser.add_argument('--carla-timeout', type=float, default=_CARLA_TIMEOUT)
    parser.add_argument('--queue-size', type=int, default=_QUEUE_SIZE)
    parser.add_argument('--fps', type=int, default=_FPS)
    parser.add_argument('--width', type=int, default=_WIDTH)
    parser.add_argument('--height', type=int, default=_HEIGHT)

    args = parser.parse_args()

    _client = carla.Client(args.carla_host, args.carla_port)
    _client.set_timeout(args.carla_timeout)

    _world = _client.get_world()
    _blueprint_library = _world.get_blueprint_library()

    _actor_id = create_sensor(_client, args.actor_id, args.sensor_blueprint_name, args.fps, args.width, args.height).id

    _sender = Sender(args.port, args.queue_size)
    _sender.start()

    _sensor = Sensor(_client, _actor_id, args.queue_size, _sender, args.client_host, args.port)
    _sensor.start()

    while 1:
        try:
            time.sleep(1)
        except KeyboardInterrupt:
            break

    _sensor.stop()
    _sender.stop()

    delete_sensor(_client, _actor_id)
