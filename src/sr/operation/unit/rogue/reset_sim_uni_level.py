from typing import ClassVar

from basic import Rect
from basic.i18_utils import gt
from sr.context import Context
from sr.operation import StateOperation, OperationOneRoundResult, Operation, StateOperationNode
from sr.operation.unit.rogue.choose_sim_uni_num import ChooseSimUniNum
from sr.operation.unit.rogue.start_sim_uni import StartSimUni
from sr.operation.unit.wait_in_world import WaitInWorld


class ResetSimUniLevel(StateOperation):

    TEMP_LEAVE: ClassVar[Rect] = Rect(0, 0, 0, 0)  # 暂离

    def __init__(self, ctx: Context):
        """
        需要在模拟宇宙 移动画面中使用
        暂离后重新进入 用于重置位置 脱困
        :param ctx:
        """
        super().__init__(
            ctx, try_times=10,
            op_name='%s %s' % (gt('模拟宇宙', 'ui'), gt('暂离重进' , 'ui')),
            nodes=[
                StateOperationNode('暂离', self._temp_leave),
                StateOperationNode('选择宇宙', self._choose_uni),
                StateOperationNode('继续挑战', self._continue),
                StateOperationNode('等待加载', self._wait),
            ]
        )

    def _temp_leave(self) -> OperationOneRoundResult:
        """
        暂离
        :return:
        """
        click = self.ocr_and_click_one_line('暂离', ResetSimUniLevel.TEMP_LEAVE, wait_after_success=5)

        if click == Operation.OCR_CLICK_SUCCESS:
            return Operation.round_success()
        else:
            self.ctx.controller.esc()  # 打开菜单
            return Operation.round_retry(status='点击暂离失败', wait=1)

    def _choose_uni(self) -> OperationOneRoundResult:
        """
        选择宇宙
        :return:
        """
        op = ChooseSimUniNum(self.ctx, num=1)  # 继续之前的 哪个宇宙没所谓
        op_result = op.execute()
        if op_result.success:
            return Operation.round_success()
        else:
            return Operation.round_fail('选择宇宙失败')

    def _continue(self) -> OperationOneRoundResult:
        """
        继续挑战
        :return:
        """
        op = StartSimUni(self.ctx)
        op_result = op.execute()
        if op_result.success:
            return Operation.round_success()
        else:
            return Operation.round_fail('继续进度失败')

    def _wait(self) -> OperationOneRoundResult:
        """
        等待加载
        :return:
        """
        op = WaitInWorld(self.ctx)
        op_result = op.execute()
        if op_result.success:
            return Operation.round_success()
        else:
            return Operation.round_fail('加载失败')