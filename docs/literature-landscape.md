# Literature and Landscape Analysis

## 2.1 Literature Analysis

**1. Does gamification work?**
* A literature review of 24 empirical studies examining gamification across contexts such as education, health and exercise, online services etc.
* The goal of the paper is to evaluate the effectiveness of gamification across different contexts, and they found that in the majority of cases, it positively affected motivation and behavior.
* However, the effects depend on the context and users, implying that gamification should be tailored and experimentally evaluated rather than universally applied.

**2. The Gamification of Learning: A Meta-analysis**
* A meta-analysis of empirical studies on gamification in learning, examining cognitive, motivational, and behavioral outcomes.
* It separates the effects of gamification into three categories:
  * Cognitive outcomes: Whether gamification actually improves learning-related performance, such as knowledge acquisition, understanding, or academic performance.
  * Motivational outcomes: Whether learners become more motivated, interested, or engaged with the learning activity.
  * Behavioral outcomes: Whether learners actually change their behavior, such as participating more, spending more time on tasks, or completing activities.
* They found small but significant positive effects of gamification on all three outcomes: cognitive learning, motivation, and learning-related behavior. The cognitive effect was the most stable when looking only at studies with stronger methodology.
* However, the motivational and behavioral effects were not robust in the higher-quality studies, and results varied considerably depending on how gamification was implemented. This implies that gamification alone is not a guaranteed solution; the specific game elements and social setup need to be designed and tested for the intended outcome.

**3. Streaks to Success? The Effects of Highlighting Streaks on Student Effort and Learning**
* The paper studies whether highlighting learning streaks can increase students’ effort and actual learning on an online math platform, using a randomized experiment with about 60,000 students. Students received either streak messages, personalized reminders, generic reminders, or no intervention.
* The goal was to test whether streaks and reminders change students’ platform use and learning. They found that both streak messages and personalized reminders increased platform use, while streak messages also produced a significant improvement in math achievement compared with the control group.
* However, the learning result comes from only about 1,500 students who completed the endline test, and the streak treatment was not significantly different from the other treatment groups on learning. This implies that streaks provide stronger evidence for increasing effort/use than for improving learning itself.

**4. Increasing Students’ Engagement to Reminder Emails Through Multi-Armed Bandits.**
* The paper studies using multi-armed bandits to adaptively choose between different reminder-email subject lines for students in a first-year CS course, with email open rate as the engagement measure.
* The goal is to test whether adaptive bandit allocation can improve student engagement with reminder emails compared with uniform random assignment. They found that bandit-based allocation could shift more students toward subject lines that appeared more effective, but the results also showed that the bandit could favor an arm even when there was no statistically significant difference between the arms.
* The main catch is the exploration–exploitation trade-off: a bandit may exploit an apparently good option too early and therefore collect insufficient evidence about the alternatives. The study also used email opens rather than learning outcomes, and was conducted in a single CS1 course.

**5. Effect of Personalized Email-Based Reminders on Participants’ Timeliness in an Online Education Program: Randomized Controlled Trial**
* The paper studies whether personalized email reminders can improve the timeliness of adult learners’ coursework, comparing personalized reminders with general reminders in an online education program. The study involved 39 adult professionals from Ethiopia, Rwanda, and Kenya.
* The goal was to test whether adding information about a learner’s current progress to regular reminders would help them stay on schedule. They found that personalized reminders increased the estimated probability of being on time by 14 percentage points compared with general reminders.
* However, both groups received reminders, the sample was only 39 learners, and all participants were adult professionals from three East African countries. The study also measured timeliness rather than learning itself. This implies that personalization can improve adherence, but its effect on learning and in other learner populations still needs to be tested.

## 2.2 Landscape Analysis

**1. Duolingo:**
* A proprietary language-learning app that uses gamification as part of its learner experience, rather than offering a gamification service for other platforms to integrate.
* Features it offers: Weekly leagues, streaks, reminders, social quests, XP, and opt-out controls. Duolingo also describes testing features through internal A/B experiments.
* Duolingo is a useful example of learner-facing features and experimentation, but it is a closed product, not a reusable backend service. Its public materials don’t reveal its event model, replay and recovery mechanisms, detailed abuse-detection methods, or notification infrastructure.

**2. Funifier:**
* A commercial gamification platform that can be integrated with business and learning applications.
* Features it offers: A REST API, SDKs, triggers, integrations, widgets, configurable game strategies, and features such as points, challenges, and leaderboards. Its case studies include learning and training uses.
* Funifier appears to cover configurable gamification and integration, but its public materials don’t establish your deeper technical goals: a versioned event model, deterministic replay and recovery, detailed abuse detection, or controlled experiments with learning and fatigue measures.

**3. FGPE Gamification Service:**
* An open-source GraphQL service for adding gamification to programming education. It uses GEdIL, a format for describing gamification rules and learning activities.
* Features it offers: It connects gamified challenges to automatically assessed programming activities. Its public repository includes the service source code and Docker-based setup.
* FGPE is focused on programming courses and challenge-based learning. The materials reviewed don’t establish a general event-processing system, cohort and global leaderboards, multi-channel notifications, abuse detection, policy experiments, or high-volume replay and recovery.

### 2.2.1 Synthetic Population Design and Bias Avoidance

To prevent simulator bias (where the generator guarantees the intervention policy wins), the simulator strictly isolates baseline behavior from intervention pathing:
* **Independent Learner Personas:** Four fixed behavioral archetypes (consistent, binge-then-lapse, weekend-only, at-risk) are defined with independently sampled parameters (session frequency, mastery growth, baseline dropout probability) before any policy selection.
* **Baseline Behavior Generation:** The baseline activity stream is generated first from a fixed random seed with zero knowledge of notifications.
* **Bounded Perturbation:** Notifications apply a capped, independently sampled effect (≤ ±3–5%) on next-day return probability rather than a hardcoded outcome.
* **Common Random Numbers:** Treatment and control arms share the exact same underlying persona population and baseline draws.
* **Sensitivity Sweeps:** Policy decisions are tested across parameter ranges; results that flip under small adjustments are marked inconclusive.
* **Reproducibility & Epistemic Limit:** Parameters and seeds are versioned with the event log for byte-for-byte re-runs. Simulations validate system mechanisms rather than real-world educational causality.
