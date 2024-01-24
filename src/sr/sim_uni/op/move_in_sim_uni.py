import time
from typing import Tuple, Optional, Callable, List, ClassVar

import numpy as np
from cv2.typing import MatLike

from basic import Point, cal_utils, str_utils
from basic.i18_utils import gt
from basic.img import MatchResult, cv2_utils
from basic.log_utils import log
from sr import cal_pos
from sr.const import game_config_const, STANDARD_RESOLUTION_W
from sr.context import Context
from sr.control import GameController
from sr.image.image_holder import ImageHolder
from sr.image.sceenshot import LargeMapInfo, MiniMapInfo, large_map, mini_map, screen_state
from sr.operation import OperationResult, OperationOneRoundResult, Operation, StateOperation, StateOperationNode, \
    StateOperationEdge
from sr.operation.unit.interact import Interact
from sr.operation.unit.move import MoveDirectly
from sr.sim_uni.op.sim_uni_battle import SimUniEnterFight
from sr.sim_uni.sim_uni_const import SimUniLevelTypeEnum, SimUniLevelType, level_type_from_id
from sr.sim_uni.sim_uni_priority import SimUniBlessPriority, SimUniNextLevelPriority
from sr.sim_uni.sim_uni_route import SimUniRoute


class MoveDirectlyInSimUni(MoveDirectly):
    """
    从当前位置 朝目标点直线前行
    有简单的脱困功能

    模拟宇宙专用
    - 不需要考虑特殊点
    - 不需要考虑多层地图
    - 战斗后需要选择祝福
    """
    def __init__(self, ctx: Context, lm_info: LargeMapInfo,
                 start: Point, target: Point,
                 bless_priority: Optional[SimUniBlessPriority] = None,
                 stop_afterwards: bool = True,
                 no_run: bool = False,
                 no_battle: bool = False,
                 op_callback: Optional[Callable[[OperationResult], None]] = None,
                 ):
        MoveDirectly.__init__(
            self,
            ctx, lm_info,
            start, target, stop_afterwards=stop_afterwards,
            no_run=no_run, no_battle=no_battle,
            op_callback=op_callback)
        self.op_name = '%s %s' % (gt('模拟宇宙', 'ui'), gt('移动 %s -> %s') % (start, target))
        self.bless_priority: SimUniBlessPriority = bless_priority

    def cal_pos(self, mm: MatLike, now_time: float) -> Tuple[Optional[Point], MiniMapInfo]:
        """
        根据上一次的坐标和行进距离 计算当前位置坐标
        :param mm: 小地图截图
        :param now_time: 当前时间
        :return:
        """
        # 根据上一次的坐标和行进距离 计算当前位置
        if self.last_rec_time > 0:
            move_time = now_time - self.last_rec_time
            if move_time < 1:
                move_time = 1
        else:
            move_time = 1
        move_distance = self.ctx.controller.cal_move_distance_by_time(move_time, run=self.run_mode != game_config_const.RUN_MODE_OFF)
        last_pos = self.pos[len(self.pos) - 1] if len(self.pos) > 0 else self.start_pos
        possible_pos = (last_pos.x, last_pos.y, move_distance)
        log.debug('准备计算人物坐标 使用上一个坐标为 %s 移动时间 %.2f 是否在移动 %s', possible_pos,
                  move_time, self.ctx.controller.is_moving)
        lm_rect = large_map.get_large_map_rect_by_pos(self.lm_info.gray.shape, mm.shape[:2], possible_pos)

        mm_info = mini_map.analyse_mini_map(mm, self.ctx.im)

        if len(self.pos) == 0:
            return self.start_pos, mm_info

        next_pos = cal_pos.cal_character_pos_for_sim_uni(self.ctx.im, self.lm_info, mm_info,
                                                         lm_rect=lm_rect, running=self.ctx.controller.is_moving)
        if next_pos is None:
            log.error('无法判断当前人物坐标 使用上一个坐标为 %s 移动时间 %.2f 是否在移动 %s', possible_pos, move_time,
                      self.ctx.controller.is_moving)
        return next_pos, mm_info

    def check_enemy_and_attack(self, mm: MatLike) -> Optional[OperationOneRoundResult]:
        """
        从小地图检测敌人 如果有的话 进入索敌
        :param mm:
        :return: 是否有敌人
        """
        if self.no_battle:
            return None
        if self.last_auto_fight_fail:  # 上一次索敌失败了 可能小地图背景有问题 等待下一次进入战斗画面刷新
            return None
        if not mini_map.is_under_attack(mm):
            return None
        self.ctx.controller.stop_moving_forward()  # 先停下来再攻击
        fight = SimUniEnterFight(self.ctx, self.bless_priority)
        op_result = fight.execute()
        if not op_result.success:
            return Operation.round_fail(status=op_result.status, data=op_result.data)
        self.last_auto_fight_fail = (op_result.status == SimUniEnterFight.STATUS_ENEMY_NOT_FOUND)
        self.last_battle_time = time.time()
        self.last_rec_time = time.time()  # 战斗可能很久 需要重置一下记录坐标时间

        return Operation.round_wait()


class MoveToNextLevel(Operation):

    MOVE_TIME: ClassVar[float] = 1.5  # 每次移动的时间
    CHARACTER_CENTER: ClassVar[Point] = Point(960, 920)  # 界面上人物的中心点 取了脚底

    def __init__(self, ctx: Context,
                 next_level_priority: Optional[SimUniNextLevelPriority] = None):
        """
        朝下一层入口走去 并且交互
        :param ctx:
        :param next_level_priority: 下一楼层类型优先级
        """
        super().__init__(ctx, try_times=5,
                         op_name='%s %s' % (gt('模拟宇宙', 'ui'), gt('向下一层移动', 'ui')))
        self.level_priority: Optional[SimUniNextLevelPriority] = next_level_priority
        self.is_moving: bool = False  # 是否正在移动
        self.start_move_time: float = 0  # 开始移动的时间
        self.interacted: bool = False  # 是否已经交互了

    def _init_before_execute(self):
        super()._init_before_execute()
        self.is_moving = False
        self.interacted: bool = False

    def _execute_one_round(self) -> OperationOneRoundResult:
        screen = self.screenshot()

        if self.interacted:
            if not screen_state.is_normal_in_world(screen, self.ctx.im):
                # 兜底 - 如果已经不在大世界画面了 就认为成功了
                return Operation.round_success()
            else:
                self.interacted = False

        interact = self._try_interact(screen)
        if interact is not None:
            return interact

        if self.is_moving:
            if time.time() - self.start_move_time > MoveToNextLevel.MOVE_TIME:
                self.ctx.controller.stop_moving_forward()
                self.is_moving = False
            return Operation.round_wait()
        else:
            type_list = MoveToNextLevel.get_next_level_type(screen, self.ctx.ih)
            if len(type_list) == 0:  # 当前没有入口 随便旋转看看
                self.ctx.controller.turn_by_angle(90)
                return Operation.round_retry('未找到下一层入口', wait=1)

            target = self._get_target_entry(type_list)

            self._move_towards(target)
            return Operation.round_wait(wait=0.1)

    @staticmethod
    def get_next_level_type(screen: MatLike, ih: ImageHolder) -> List[MatchResult]:
        """
        获取当前画面中的下一层入口
        MatchResult.data 是对应的类型 SimUniLevelType
        :param screen: 屏幕截图
        :param ih: 图片加载器
        :return:
        """
        source_kps, source_desc = cv2_utils.feature_detect_and_compute(screen)

        result_list: List[MatchResult] = []

        for enum in SimUniLevelTypeEnum:
            level_type: SimUniLevelType = enum.value
            template = ih.get_sim_uni_template(level_type.template_id)

            result = cv2_utils.feature_match_for_one(source_kps, source_desc,
                                                     template.kps, template.desc,
                                                     template.origin.shape[1], template.origin.shape[0],
                                                     knn_distance_percent=0.6)

            if result is None:
                continue

            result.data = level_type
            result_list.append(result)

        return result_list

    def _get_target_entry(self, type_list: List[MatchResult]) -> MatchResult:
        """
        获取需要前往的入口
        :param type_list: 入口类型
        :return:
        """
        idx = MoveToNextLevel.match_best_level_type(type_list, self.level_priority)
        return type_list[idx]

    @staticmethod
    def match_best_level_type(type_list: List[MatchResult], level_priority: Optional[SimUniNextLevelPriority]) -> int:
        """
        根据优先级 获取最优的入口类型
        :param type_list: 入口类型 保证长度大于0
        :param level_priority: 优先级
        :return: 下标
        """
        if level_priority is None:
            return 0

        for priority_id in level_priority.id_list:
            priority_level_type = level_type_from_id(priority_id)
            if priority_level_type is None:
                continue
            for idx, type_pos in enumerate(type_list):
                if type_pos.data == priority_level_type:
                    return idx

        return 0

    def _move_towards(self, target: MatchResult):
        """
        朝目标移动 先让人物转向 让目标就在人物前方
        :param target:
        :return:
        """
        angle_to_turn = self._get_angle_to_turn(target)
        self.ctx.controller.turn_by_angle(angle_to_turn)
        time.sleep(0.5)
        self.ctx.controller.start_moving_forward()
        self.start_move_time = time.time()
        self.is_moving = True

    def _get_angle_to_turn(self, target: MatchResult) -> float:
        """
        获取需要转向的角度
        角度的定义与 game_controller.turn_by_angle 一致
        正数往右转 人物角度增加；负数往左转 人物角度减少
        :param target:
        :return:
        """
        # 小地图用的角度 正右方为0 顺时针为正
        mm_angle = cal_utils.get_angle_by_pts(MoveToNextLevel.CHARACTER_CENTER, target.center)

        return mm_angle - 270

    def _try_interact(self, screen: MatLike) -> Optional[OperationOneRoundResult]:
        """
        尝试交互
        :param screen:
        :return:
        """
        if self._can_interact(screen):
            self.ctx.controller.stop_moving_forward()
            self.ctx.controller.interact(interact_type=GameController.MOVE_INTERACT_TYPE)
            self.interacted = True
            return Operation.round_wait(wait=0.25)
        else:
            return None

    def _can_interact(self, screen: MatLike) -> bool:
        """
        当前是否可以交互
        :param screen: 屏幕截图
        :return:
        """
        part, _ = cv2_utils.crop_image(screen, Interact.SINGLE_LINE_INTERACT_RECT)
        # ocr_result = self.ctx.ocr.match_one_best_word(part, '区域', lcs_percent=0.1)
        # return ocr_result is not None
        ocr_result = self.ctx.ocr.ocr_for_single_line(part)
        return str_utils.find_by_lcs(gt('区域', 'ocr'), ocr_result)


class MoveToNextLevelByRoute(StateOperation):

    def __init__(self, ctx: Context, route: SimUniRoute, current_pos: Point,
                 next_level_priority: Optional[SimUniNextLevelPriority] = None,
                 ):
        """
        朝下一层入口走去 并且交互
        依赖路线配置的坐标
        :param ctx:
        :param route: 路线配置
        :param current_pos: 当前位置
        :param next_level_priority: 下一楼层类型优先级
        """

        turn = StateOperationNode('转向', self._turn_to_entry)
        get_pos = StateOperationNode('获取目标点', self._get_target_pos)
        move = StateOperationNode('移动', self._get_target_pos)
        interact = StateOperationNode('交互', self._interact)

        super().__init__(ctx, try_times=5,
                         op_name='%s %s' % (gt('模拟宇宙', 'ui'), gt('向下一层移动', 'ui')),
                         nodes=[turn, get_pos, move, interact]
                         )
        self.level_priority: Optional[SimUniNextLevelPriority] = next_level_priority
        self.route: SimUniRoute = route
        self.current_pos: Point = current_pos
        self.target_pos: Optional[Point] = None

    def _init_before_execute(self):
        super()._init_before_execute()
        self.target_pos = None

    def _turn_to_entry(self) -> OperationOneRoundResult:
        """
        转动到面向下一层入口的方向
        :return:
        """
        if len(self.route.event_pos_list) == 0:
            return Operation.round_fail('未配置入口坐标')

        # 转向中间点
        avg_pos_x = np.mean([pos.x for pos in self.route.event_pos_list], dtype=np.uint8)
        avg_pos_y = np.mean([pos.y for pos in self.route.event_pos_list], dtype=np.uint8)
        target_pos = Point(avg_pos_x, avg_pos_y)

        screen = self.screenshot()
        mm = mini_map.cut_mini_map(screen)
        mm_info: MiniMapInfo = mini_map.analyse_mini_map(mm, self.ctx.im)
        self.ctx.controller.turn_by_pos(self.current_pos, target_pos, mm_info.angle)

        return Operation.round_success(wait=0.5)  # 等待转动完成

    def _get_target_pos(self) -> OperationOneRoundResult:
        """
        获取目标入口的坐标
        :return:
        """
        if len(self.route.next_pos_list) == 1:
            self.target_pos = self.route.next_pos_list[0]
            return Operation.round_success()
        screen = self.screenshot()
        next_level_type_list = MoveToNextLevel.get_next_level_type(screen, self.ctx.ih)
        if len(next_level_type_list) == 0:
            return Operation.round_retry('识别不到下一层入口', wait=0.5)
        priority_entry = MoveToNextLevel.match_best_level_type(next_level_type_list, self.level_priority)

        # 是否在左边
        target_on_left = (STANDARD_RESOLUTION_W // 2) > next_level_type_list[priority_entry].center.x

        screen = self.screenshot()
        mm = mini_map.cut_mini_map(screen)
        mm_info: MiniMapInfo = mini_map.analyse_mini_map(mm, self.ctx.im)

        for pos in self.route.next_pos_list:
            opt_angle = cal_utils.get_angle_by_pts(self.current_pos, pos)
            opt_on_left = opt_angle < mm_info.angle and mm_info.angle - opt_angle < 180
            if target_on_left == opt_on_left:
                self.target_pos = pos
                return Operation.round_success()

        return Operation.round_retry('匹配入口坐标失败', wait=0.5)

    def _move(self) -> OperationOneRoundResult:
        """
        朝入口移动
        :return:
        """
        op = MoveDirectlyInSimUni(self.ctx, self.ctx.ih.get_large_map(self.route.region),
                                  start=self.current_pos, target=self.target_pos,
                                  no_run=True, no_battle=True
                                  )
        op_result = op.execute()
        return Operation.round_by_op(op_result)

    def _interact(self) -> OperationOneRoundResult:
        """
        交互
        :return:
        """
        op = Interact(self.ctx, '区域', single_line=True, lcs_percent=0.1)
        op_result = op.execute()
        return Operation.round_by_op(op_result)


class MoveToMiniMapInteractIcon(Operation):

    STATUS_ICON_NOT_FOUND: ClassVar[str] = '未找到图标'

    def __init__(self, ctx: Context, icon_template_id: str, interact_word: str):
        """
        朝小地图上的图标走去 并交互
        :param ctx:
        """
        super().__init__(ctx, try_times=5,
                         op_name='%s %s' % (
                             gt('模拟宇宙', 'ui'), 
                             gt('走向%s' % interact_word, 'ui'))
                         )

        self.icon_template_id: str = icon_template_id
        self.interact_word: str = interact_word

    def _execute_one_round(self) -> OperationOneRoundResult:
        screen = self.screenshot()

        if not screen_state.is_normal_in_world(screen, self.ctx.im):
            log.info('未在大世界')
            return Operation.round_success()

        interact = self._try_interact(screen)
        if interact is not None:
            return interact

        mm = mini_map.cut_mini_map(screen)
        target_pos = self._get_event_pos(mm)

        if target_pos is None:
            log.info('无图标')
            return Operation.round_retry(MoveToMiniMapInteractIcon.STATUS_ICON_NOT_FOUND, wait=0.5)
        else:
            start_pos = Point(mm.shape[1] // 2, mm.shape[0] // 2)
            angle = mini_map.analyse_angle(mm)
            self.ctx.controller.move_towards(start_pos, target_pos, angle)
            return Operation.round_wait()

    def _get_event_pos(self, mm: MatLike) -> Optional[Point]:
        """
        获取时间图标的位置
        :param mm:
        :return:
        """
        angle = mini_map.analyse_angle(mm)
        radio_to_del = mini_map.get_radio_to_del(self.ctx.im, angle)
        mm_del_radio = mini_map.remove_radio(mm, radio_to_del)
        source_kps, source_desc = cv2_utils.feature_detect_and_compute(mm_del_radio)
        template = self.ctx.ih.get_template(self.icon_template_id, sub_dir='sim_uni')
        mr = cv2_utils.feature_match_for_one(source_kps, source_desc,
                                             template.kps, template.desc,
                                             template.origin.shape[1], template.origin.shape[0])

        return None if mr is None else mr.center

    def _try_interact(self, screen: MatLike) -> Optional[OperationOneRoundResult]:
        """
        尝试交互
        :param screen:
        :return:
        """
        if self._can_interact(screen):
            self.ctx.controller.stop_moving_forward()
            self.ctx.controller.interact(interact_type=GameController.MOVE_INTERACT_TYPE)

            return Operation.round_wait(wait=0.25)
        else:
            return None

    def _can_interact(self, screen: MatLike) -> bool:
        """
        当前是否可以交互
        :param screen: 屏幕截图
        :return:
        """
        part, _ = cv2_utils.crop_image(screen, Interact.SINGLE_LINE_INTERACT_RECT)
        # ocr_result = self.ctx.ocr.match_one_best_word(part, self.interact_word, lcs_percent=0.1)
        # return ocr_result is not None

        ocr_result = self.ctx.ocr.ocr_for_single_line(part)
        return str_utils.find_by_lcs(gt(self.interact_word, 'ocr'), ocr_result)

    def _after_operation_done(self, result: OperationResult):
        super()._after_operation_done(result)
        self.ctx.controller.stop_moving_forward()

    def on_pause(self):
        super().on_pause()
        self.ctx.controller.stop_moving_forward()


class MoveToHertaInteract(MoveToMiniMapInteractIcon):
    
    def __init__(self, ctx: Context):
        super().__init__(ctx, 'mm_sp_herta', '黑塔')


class MoveToEventInteract(MoveToMiniMapInteractIcon):

    def __init__(self, ctx: Context):
        super().__init__(ctx, 'mm_sp_event', '事件')
