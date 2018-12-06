import numpy
import pygame
import pygame.locals
from grid import Grid
from person import Player
import time
import sys
import asyncio

if sys.version_info < (3, 7):
    raise RuntimeError('Python3.7+ needed to run.')


# TODO perf bench compared to synchronously managed tasks

def get_direction(direction):
    """
    :param direction: A direction input array representing keyboard arrows
        entry, get from LlnRpg object.
    :return: The direction represented by the input array. 1: UP, 2: DOWN,
        3: LEFT, 4: RIGHT.
    """
    mindir = max(direction) + 1
    curdir = 0
    for i in range(0, len(direction)):
        if 0 < direction[i] < mindir:
            mindir = direction[i]
            curdir = i + 1
    return curdir


def get_animated_sprite(person, grid, coord):
    """
    :param person: A Person object, supposed to be in grid.
    :param grid: A Grid object.
    :param coord: The coordinates of the Person object the grid's (0, 0),
        usually the top left corner.
    :return: The sprite that should be displayed for the Person object,
        given its posture and coordinates.
    """
    i = int(person.direction < 3)
    coord, tilesize = coord[i], grid.tilesize[i]
    sprite = int(coord*person.sprites_speed/tilesize) % len(person.posture)
    return person.posture[sprite]


class LlnRpg:
    """
    A class gathering all the information needed by the game to run. This class
    act as a place to store everything we need for our asynchronous functions
    without need for a large amount of argument or global variables.

    Some of the functions from this class are asynchronous. If you want to
    dig into the code and the way it works, you will first need to see the
    python doc's section about asynchronous programming in python.

    :[class] monitoring_data: A dictionary storing monitored data about the
        game execution.
    :grid_width: The width (in tiles) of the grid.
    :grid_height: The height (in tiles) of the grid.
    :screen: The pygame.Surface object representing the screen.
    :grid: The Grid object representing the map.
    :player: the Player object representing the player.
    :sound_button: The pygame.Surface object representing the sound button.
    :sound_button_box: The pygame.Rect object representing the sound button's
        hitbox.
    :sound_played: True if sound is played, False if muted.
    :running: True if game is running, False otherwise.

    """
    key_direction_mapping = {
        pygame.locals.K_UP: 0,
        pygame.locals.K_DOWN: 1,
        pygame.locals.K_LEFT: 2,
        pygame.locals.K_RIGHT: 3,
        }

    monitoring_data = {
        'events': 0.0,
        'handled-events': 0.0,
        'clicks': 0.0,
        'handled-clicks': 0.0,
        'frames': 0.0,
        'handle_events-loops': 0.0,
        'monitoring-interval': 0.0,
        }

    def __init__(self, **kwargs):
        """
        All arguments are keyword-arguments. All have default values.

        :param grid_width: Grid width in tiles.
        :param grid_height: Grid height in tiles.
        :param screen_mode: Screen size.
        :param map_file: .map file describing the map.
        :param map_pos: coordinates of the top left corner of the map,
            relative to the top left corner of the screen.
        :param player: The Player object representing the player.
        :param play_sound: True to start playing sound, False to start with
            sound muted.
        :param base_delay: Minimal delay to wait between each asynchronous
            call of the same function. Useless to give < 1e-4. This should
            not be changed since it affects a lot the way the game behave and
            its performances.
        """
        self.grid_width = kwargs.get('grid_width', 30)
        self.grid_height = kwargs.get('grid_height', 20)

        self.screen = pygame.display.set_mode(kwargs.get('screen_mode',
                                                         (960, 640)))
        self.grid = Grid(kwargs.get('map_file', "level.map"),
                         self.screen,
                         (self.grid_width, self.grid_height),
                         kwargs.get('map_pos', (32*4, 32*2)))
        self.player = kwargs.get('player', Player(['res/trainer_walking.png',
                                                   'res/trainer_running.png'],
                                                  self.grid.tilesize, 1))

        # Arrays storing keyboard inputs from arrow keys, to allow smooth
        # movement of the player between each tile and avoid being in between
        # two tiles unexpectedly
        self.current_direction = [0, 0, 0, 0]
        self.old_current_direction = self.current_direction.copy()

        # init button and its hitbox variable, assigned in toggle_sound
        self.sound_button = self.sound_button_box = None

        self.sound_played = not kwargs.get('play_sound', False)
        self.running = False
        # Useless  to go < 1e-4, this controls game tick speed
        # (roughly, not the same as fps)
        # Think of it as "how fast will the game compute things"
        self.base_delay = kwargs.get('base_delay', 1e-3)

    async def monitoring(self):
        """
        An asynchronous loop function used for monitoring and debug. Data
        entries can be added to the class-attribute monitoring_data and
        processed and/or printed out in this function.

        This function may also be used to check program's status and sanity.
        """
        while self.running:

            # Computing elapsed time during the asynchronous waiting time
            elapsed = time.time()
            await asyncio.sleep(1)
            elapsed = time.time() - elapsed

            # Printing the whole data dictionary
            self.monitoring_data['monitoring-interval'] = elapsed
            print('MONITORING:')
            for k, v in self.monitoring_data.items():
                print('[] ' + k + ': ' + str(v))
            print('')

            # Resetting
            for k, v in self.monitoring_data.items():
                self.monitoring_data[k] = 0.0
        print('Closed monitoring')

    async def handle_mouse(self):
        """
        An asynchronous loop function to handle mouse interactions.

        This functions check when the user clicks and tell the game what to
        do then.
        """
        while self.running:
            # Wait until mouse left clicks
            # get_pressed returns a 3-tuple for left, middle and right click.
            while self.running and not (pygame.mouse.get_focused()
                                        and pygame.mouse.get_pressed()[0]):
                await asyncio.sleep(self.base_delay)
            # Get mouse position relative to top left corner of the screen
            x, y = pygame.mouse.get_pos()
            # If click was on the sound button
            if self.sound_button_box.collidepoint(x, y):
                self.toggle_sound()
                self.monitoring_data['handled-clicks'] += 1
            # wait until mouse unpressed
            # TODO solve bug happening when leaving focus but still pressed
            while self.running and (pygame.mouse.get_focused()
                                    and pygame.mouse.get_pressed()[0]):
                await asyncio.sleep(self.base_delay)
            self.monitoring_data['clicks'] += 1
            await asyncio.sleep(self.base_delay)  # avoiding too fast spam click
        print('Closed mouse handler')

    async def handle_graphics(self):
        """
        An asynchronous loop function that draws map and sprites onto the
        screen. This function calls all the update functions of the entities
        in the map, including the player, first, then draw.
        """
        # Init entities
        self.init_player()
        while self.running:
            # Update entities
            player_coord = self.update_player()

            # Draw map in the background
            self.screen.blit(self.grid.background, self.grid.view_coord)
            # Draw sound button
            self.screen.blit(self.sound_button, (0, 0))
            # Draw entities
            self.screen.blit(
                self.player.sprites[get_animated_sprite(
                    self.player, self.grid, player_coord)],
                (int(self.player.screen_pos[0]),
                 int(self.player.screen_pos[1])))
            # Actually display what was drawn
            pygame.display.flip()
            self.monitoring_data['frames'] += 1
            # Wait until next frame
            await asyncio.sleep(0.015)  # This controls fps (roughly)
        print('Closed graphics handler')

    async def handle_events(self):
        """
        An asynchronous loop function that process events.
        """
        while self.running:
            # Poll each event pushed to the event queue
            for event in pygame.event.get():
                # Quit event
                if event.type == pygame.QUIT:
                    self.running = False
                    self.monitoring_data['handled-events'] += 1
                # Key pressed event, arrow key pressed
                if event.type == pygame.locals.KEYDOWN \
                        and event.key in self.key_direction_mapping:
                    # Update pressed key as being the last pressed key
                    # (in case several are pressed simultaneously)
                    self.current_direction[self.key_direction_mapping[
                        event.key]] = max(self.current_direction) + 1
                    self.monitoring_data['handled-events'] += 1
                # Key unpressed event, arrow key unpressed
                elif event.type == pygame.locals.KEYUP \
                        and event.key in self.key_direction_mapping:
                    # Key is unpressed, so it should be taken into account
                    # anymore in player direction computation
                    self.current_direction[self.key_direction_mapping[
                        event.key]] = 0
                    self.monitoring_data['handled-events'] += 1
                self.monitoring_data['events'] += 1
            self.monitoring_data['handle_events-loops'] += 1
            await asyncio.sleep(self.base_delay)
        print('Closed events handler')

    def main(self):
        """
        Main function that initiates and runs the game. Launch asynchronous
        tasks and wait them to finish.
        """
        pygame.init()
        pygame.display.set_caption("RPG - Louvain-la-Neuve")

        # Draw the map on the screen, as a background
        self.screen.blit(self.grid.background, self.grid.view_coord)
        pygame.display.flip()

        # load music
        pygame.mixer.init()
        pygame.mixer.music.load("sound/lln_sound.wav")
        self.toggle_sound()

        async def gather_tasks():
            # Inner function to gather all coroutines in a single awaitable.
            await asyncio.gather(
                self.handle_events(),
                self.handle_mouse(),
                self.handle_graphics(),
                self.monitoring()
                )

        # Start running the game
        self.running = True

        asyncio.run(gather_tasks())
        print('Exit main')

    def init_player(self):
        """
        Initiates the player object into the map.
        """
        # Put the player in the center of the screen, then computes to which
        # coordinates it correspond in the map.
        # The player position is adjusted so that it is precisely fitting in
        # a tile, even if he is no longer precisely in the center of the screen
        size = self.screen.get_size()
        x = (size[0] / 2 - self.grid.screen_pos[0]) // self.grid.tilesize[0]
        y = (size[1] / 2 - self.grid.screen_pos[1]) // self.grid.tilesize[1]
        self.player.pos = int(x), int(y)
        # Computes back the adjusted coordinates of the player onto the screen
        x = x * self.grid.tilesize[0] + self.grid.screen_pos[0]
        y = y * self.grid.tilesize[1] + self.grid.screen_pos[1]
        self.player.screen_pos = int(x), int(y)
        # Initiates posture and direction because needed
        self.player.posture = 'still'
        self.player.direction = 1

    def update_player(self):
        """
        Updates the position, direction and posture of the player, given its
        position in the map.
        :return: The new coordinates of the player on the map, in screen pixels.
        """
        # Position indicated by keybard input
        direction = get_direction(self.current_direction)
        # Offset from the position fitting precisely into a tile
        grid_offset_x, grid_offset_y = self.grid.get_mod()
        # Position of the player on the map, in screen pixels
        pos_x = self.player.screen_pos[0] - self.grid.view_coord[0]
        pos_y = self.player.screen_pos[1] - self.grid.view_coord[1]

        # If player is fitting onto a tile
        if grid_offset_y + grid_offset_x == 0:
            # Update its position to this tile
            self.player.pos = int(pos_x / self.grid.tilesize[0]), \
                              int(pos_y / self.grid.tilesize[1])
            # Update its direction
            self.player.direction = direction

            # If he wants to move (direction !=0) and if he can move (no
            # obstacles in front of him), update the view coordinates
            if direction != 0 and self.check_collision(direction):
                # Update its posture
                if self.player.running:
                    self.player.posture = 'running'
                else:
                    self.player.posture = 'walking'
                # And move
                self.update_view_coordinates(direction)
            # Otherwise, avoid him to move
            else:
                self.player.posture = 'still'
            # Remember the direction for future movements
            self.old_current_direction = self.current_direction.copy()
        # If player is between two tiles, ignore keyboard entries and
        # continue straight forward to next tile
        else:
            self.update_view_coordinates(get_direction(
                self.old_current_direction))

        return self.player.screen_pos[0] - self.grid.view_coord[0], \
            self.player.screen_pos[1] - self.grid.view_coord[1]

    def toggle_sound(self):
        """
        Toggles the sound playing and the appearance of the corresponding
        button at each call.
        """
        if self.sound_played:
            self.sound_button = pygame.image.load("images/no_sound_icon.png")
            self.sound_button = pygame.transform.scale(self.sound_button,
                                                       (32, 32))
            pygame.mixer.music.stop()
            self.sound_played = False
        else:
            self.sound_button = pygame.image.load("images/sound_icon.png")
            self.sound_button = pygame.transform.scale(self.sound_button,
                                                       (32, 32))
            pygame.mixer.music.play(-1, 0.0)
            self.sound_played = True
        if self.sound_button_box is None:
            self.sound_button_box = self.sound_button.get_rect()

    def update_view_coordinates(self, direction):
        """
        Updates the view coordinates toward the opposite of given direction,
        which means that the player will move toward this direction relative
        to the grid.
        """
        speed = self.player.speed
        if self.player.running:
            speed *= 2
        if direction == 1:
            self.grid.view_coord = (self.grid.view_coord[0],
                                    self.grid.view_coord[1] + speed)
        elif direction == 2:
            self.grid.view_coord = (self.grid.view_coord[0],
                                    self.grid.view_coord[1] - speed)
        elif direction == 3:
            self.grid.view_coord = (self.grid.view_coord[0] + speed,
                                    self.grid.view_coord[1])
        elif direction == 4:
            self.grid.view_coord = (self.grid.view_coord[0] - speed,
                                    self.grid.view_coord[1])

    def check_collision(self, direction=None):
        """
        Check for collisions in front of the player. If no direction is
        specified, take the direction stored from last frame.

        :return: True if player can move forward, False if faces a wall.
        """
        if direction is None:
            direction = get_direction(self.old_current_direction)
        x, y = self.player.pos
        if direction == 1:
            y -= 1
        if direction == 2:
            y += 1
        if direction == 3:
            x -= 1
        if direction == 4:
            x += 1
        return self.grid.level.map[y][x] not in ('o',)


if __name__ == "__main__":
    game = LlnRpg()
    game.main()
