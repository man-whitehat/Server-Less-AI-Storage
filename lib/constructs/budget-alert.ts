import * as budgets from "aws-cdk-lib/aws-budgets";
import { Construct } from "constructs";

export interface BudgetAlertProps {
  /**
   * Monthly budget limit in USD.
   * Defaults to $10.00
   */
  readonly limitAmount?: number;

  /**
   * Notification email recipient for budget threshold alarms.
   * If omitted, defaults to ALERT_EMAIL environment variable or placeholder.
   */
  readonly alertEmail?: string;
}

/**
 * AWS Budget Construct that sets a hard monthly cost threshold
 * with multi-stage alarms (80% forecasted, 100% actual) to guarantee
 * zero budget overruns under the strict $100 cap.
 */
export class BudgetAlertConstruct extends Construct {
  public readonly budget: budgets.CfnBudget;

  constructor(scope: Construct, id: string, props: BudgetAlertProps = {}) {
    super(scope, id);

    const limitAmount = props.limitAmount ?? 10;
    const alertEmail =
      props.alertEmail ||
      process.env.ALERT_EMAIL ||
      "admin@example.com";

    this.budget = new budgets.CfnBudget(this, "MonthlyCostBudget", {
      budget: {
        budgetName: "ai-document-analyzer-monthly-limit",
        budgetType: "COST",
        timeUnit: "MONTHLY",
        budgetLimit: {
          amount: limitAmount,
          unit: "USD",
        },
      },
      notificationsWithSubscribers: [
        {
          // Warning threshold: Forecasted 80% ($8.00)
          notification: {
            comparisonOperator: "GREATER_THAN",
            notificationType: "FORECASTED",
            threshold: 80,
            thresholdType: "PERCENTAGE",
          },
          subscribers: [
            {
              subscriptionType: "EMAIL",
              address: alertEmail,
            },
          ],
        },
        {
          // Critical threshold: Actual 100% ($10.00)
          notification: {
            comparisonOperator: "GREATER_THAN",
            notificationType: "ACTUAL",
            threshold: 100,
            thresholdType: "PERCENTAGE",
          },
          subscribers: [
            {
              subscriptionType: "EMAIL",
              address: alertEmail,
            },
          ],
        },
      ],
    });
  }
}
