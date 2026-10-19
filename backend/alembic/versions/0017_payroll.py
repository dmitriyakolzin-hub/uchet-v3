"""Начисление зарплаты за месяц (документ и строки), тариф страховых взносов у юрлица

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('payroll_runs',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('organization_id', sa.BigInteger(), nullable=False),
    sa.Column('legal_entity_id', sa.BigInteger(), nullable=False),
    sa.Column('year', sa.SmallInteger(), nullable=False),
    sa.Column('month', sa.SmallInteger(), nullable=False),
    sa.Column('norm_days', sa.SmallInteger(), nullable=False),
    sa.Column('status', sa.String(length=10), server_default='draft', nullable=False),
    sa.Column('operation_id', sa.BigInteger(), nullable=True),
    sa.Column('created_by', sa.BigInteger(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('draft', 'posted')", name=op.f('ck_payroll_runs_status')),
    sa.CheckConstraint('month BETWEEN 1 AND 12', name=op.f('ck_payroll_runs_month')),
    sa.CheckConstraint('norm_days BETWEEN 1 AND 31', name=op.f('ck_payroll_runs_norm_days')),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_payroll_runs_created_by_users'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['legal_entity_id'], ['legal_entities.id'], name=op.f('fk_payroll_runs_legal_entity_id_legal_entities'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['operation_id'], ['operations.id'], name=op.f('fk_payroll_runs_operation_id_operations'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_payroll_runs_organization_id_organizations'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_payroll_runs')),
    sa.UniqueConstraint('legal_entity_id', 'year', 'month', name='uq_payroll_runs_entity_month')
    )
    op.create_index(op.f('ix_payroll_runs_organization_id'), 'payroll_runs', ['organization_id'], unique=False)
    op.create_table('payroll_lines',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('run_id', sa.BigInteger(), nullable=False),
    sa.Column('employee_id', sa.BigInteger(), nullable=False),
    sa.Column('salary', sa.Numeric(precision=18, scale=2), server_default='0', nullable=False),
    sa.Column('work_days', sa.Numeric(precision=5, scale=2), server_default='0', nullable=False),
    sa.Column('bonus', sa.Numeric(precision=18, scale=2), server_default='0', nullable=False),
    sa.Column('vacation', sa.Numeric(precision=18, scale=2), server_default='0', nullable=False),
    sa.Column('sick', sa.Numeric(precision=18, scale=2), server_default='0', nullable=False),
    sa.Column('other_accrual', sa.Numeric(precision=18, scale=2), server_default='0', nullable=False),
    sa.Column('other_deduction', sa.Numeric(precision=18, scale=2), server_default='0', nullable=False),
    sa.Column('salary_amount', sa.Numeric(precision=18, scale=2), server_default='0', nullable=False),
    sa.Column('accrued', sa.Numeric(precision=18, scale=2), server_default='0', nullable=False),
    sa.Column('ndfl', sa.Numeric(precision=18, scale=2), server_default='0', nullable=False),
    sa.Column('insurance', sa.Numeric(precision=18, scale=2), server_default='0', nullable=False),
    sa.Column('net', sa.Numeric(precision=18, scale=2), server_default='0', nullable=False),
    sa.CheckConstraint('work_days >= 0', name=op.f('ck_payroll_lines_work_days')),
    sa.ForeignKeyConstraint(['employee_id'], ['employees.id'], name=op.f('fk_payroll_lines_employee_id_employees'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['run_id'], ['payroll_runs.id'], name=op.f('fk_payroll_lines_run_id_payroll_runs'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_payroll_lines')),
    sa.UniqueConstraint('run_id', 'employee_id', name='uq_payroll_lines_run_employee')
    )
    op.create_index(op.f('ix_payroll_lines_run_id'), 'payroll_lines', ['run_id'], unique=False)
    op.add_column('legal_entities', sa.Column('insurance_tariff', sa.String(length=10), server_default='sme', nullable=False))
    op.create_check_constraint(op.f('ck_legal_entities_insurance_tariff'), 'legal_entities',
                               "insurance_tariff IN ('sme', 'general')")


def downgrade() -> None:
    op.drop_constraint(op.f('ck_legal_entities_insurance_tariff'), 'legal_entities', type_='check')
    op.drop_column('legal_entities', 'insurance_tariff')
    op.drop_index(op.f('ix_payroll_lines_run_id'), table_name='payroll_lines')
    op.drop_table('payroll_lines')
    op.drop_index(op.f('ix_payroll_runs_organization_id'), table_name='payroll_runs')
    op.drop_table('payroll_runs')
